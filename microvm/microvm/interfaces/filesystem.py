"""FilesystemInterface — per-instance scoped filesystem.

This is the filesystem "syscall surface" for an instance. The browser process
itself still runs on the host (we're a process wrapper, not a kernel), but
every path the harness itself resolves on behalf of the instance goes through
this object. That gives us three guarantees:

  1. Each instance has its own profile dir, download dir, and read-root.
  2. Paths are confined: resolve() raises if the target escapes the root.
  3. Observability: every read/write is logged to an in-memory journal the
     caller can inspect or persist.

We deliberately do NOT try to sandbox the browser's own file I/O — doing that
portably would require OS-level sandboxing (AppContainer / sandbox-exec) and
is out of scope. What we DO guarantee is that any instance launched through
microvm sees a filesystem layout that is disjoint from every other instance.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class FSEvent:
    ts: float
    op: str          # "read" | "write" | "delete" | "mkdir"
    path: str        # path relative to root
    size: int = 0    # bytes, where meaningful


class FilesystemEscape(PermissionError):
    """Raised when a requested path would escape the instance root."""


class FilesystemInterface:
    """Scoped filesystem rooted at `root`.

    Layout under root/:
        profile/     -- browser user-data-dir
        downloads/   -- browser download target
        work/        -- general-purpose scratch
    """

    def __init__(self, root: Path):
        self._root = Path(root).resolve()
        self._lock = threading.Lock()
        self._journal: list[FSEvent] = []
        self._ensure_layout()

    # ------------------------------------------------------------------ setup
    def _ensure_layout(self) -> None:
        for sub in ("profile", "downloads", "work"):
            (self._root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths
    @property
    def root(self) -> Path:
        return self._root

    @property
    def profile_dir(self) -> Path:
        return self._root / "profile"

    @property
    def downloads_dir(self) -> Path:
        return self._root / "downloads"

    @property
    def work_dir(self) -> Path:
        return self._root / "work"

    def resolve(self, rel: str | Path) -> Path:
        """Resolve `rel` under the instance root, refusing escapes.

        Accepts a relative path; absolute paths are rejected outright to avoid
        any ambiguity about intent.
        """
        p = Path(rel)
        if p.is_absolute():
            raise FilesystemEscape(f"absolute paths are not allowed: {rel}")
        full = (self._root / p).resolve()
        try:
            full.relative_to(self._root)
        except ValueError as e:
            raise FilesystemEscape(f"path escapes instance root: {rel}") from e
        return full

    # ------------------------------------------------------------------ I/O
    def read_bytes(self, rel: str | Path) -> bytes:
        full = self.resolve(rel)
        data = full.read_bytes()
        self._log("read", rel, len(data))
        return data

    def write_bytes(self, rel: str | Path, data: bytes) -> Path:
        full = self.resolve(rel)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        self._log("write", rel, len(data))
        return full

    def delete(self, rel: str | Path) -> None:
        full = self.resolve(rel)
        if full.is_dir():
            shutil.rmtree(full)
        elif full.exists():
            full.unlink()
        self._log("delete", rel)

    def list(self, rel: str | Path = ".") -> Iterator[Path]:
        full = self.resolve(rel)
        if not full.exists():
            return iter(())
        return (p.relative_to(self._root) for p in full.rglob("*") if p.is_file())

    # ------------------------------------------------------------------ journal
    def _log(self, op: str, rel: str | Path, size: int = 0) -> None:
        with self._lock:
            self._journal.append(FSEvent(time.time(), op, str(rel), size))

    def journal(self) -> list[FSEvent]:
        with self._lock:
            return list(self._journal)

    def dump_journal(self, path: Path | None = None) -> Path:
        """Write the journal to JSONL on disk and return the path."""
        target = path or (self._root / "fs_journal.jsonl")
        with open(target, "w", encoding="utf-8") as f:
            for e in self.journal():
                f.write(json.dumps(e.__dict__) + "\n")
        return target

    # ------------------------------------------------------------------ teardown
    def wipe(self) -> None:
        """Delete everything under root. Irreversible."""
        if self._root.exists():
            shutil.rmtree(self._root)
