"""Instance — a single isolated Chromium process with its own OS surface.

An Instance owns:
  * a NetworkInterface (its own proxy, its own port, its own log)
  * a FilesystemInterface (its own profile, downloads, work dir)
  * a subprocess.Popen for the browser
  * a clean, scrubbed environment
  * a launch config describing which binary + args to use

Isolation guarantees (all enforced at launch time):
  1. Profile data      --user-data-dir points inside the instance FS root.
  2. Network traffic   --proxy-server points at the instance's private proxy.
  3. Filesystem        downloads default to the instance downloads dir.
  4. Process/env       a scrubbed env dict is passed to Popen; no host env
                       leaks unless the caller explicitly opts in.

What we do NOT guarantee: we do not prevent a malicious binary from reading
host files, escaping the proxy, or making raw socket calls. Hard sandboxing
of an uncooperative process is an OS-sandboxing problem (AppContainer on
Windows, sandbox-exec on macOS) and out of scope for a portable pure-Python
tool. "Isolated from each other" is the contract; "sandboxed from the host"
is not.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from microvm.interfaces.filesystem import FilesystemInterface
from microvm.interfaces.network import NetworkInterface
from microvm.platform_utils import find_default_browser, state_root


# Minimal env that a browser process actually needs. Anything else must be
# opted into explicitly via Instance(env_passthrough=[...]).
_BASE_ENV_KEYS_WIN = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "PATH", "PATHEXT", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "COMPUTERNAME", "USERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
_BASE_ENV_KEYS_POSIX = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
    "TMPDIR", "SHELL", "DISPLAY",
)


@dataclass
class LaunchSpec:
    """Describes how to launch the browser for this instance."""
    binary: Path
    start_url: str = "about:blank"
    extra_args: Sequence[str] = field(default_factory=tuple)
    headless: bool = False


class Instance:
    def __init__(
        self,
        name: str | None = None,
        binary: str | Path | None = None,
        start_url: str = "about:blank",
        extra_args: Sequence[str] = (),
        headless: bool = False,
        env_passthrough: Sequence[str] = (),
        extra_env: dict[str, str] | None = None,
        root: Path | None = None,
        blocklist: set[str] | None = None,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.name = name or f"inst-{self.id}"

        # --- resolve binary ------------------------------------------------
        bin_path = Path(binary) if binary else find_default_browser()
        if bin_path is None:
            raise FileNotFoundError(
                "No Chromium-family binary found. Pass binary=... explicitly."
            )
        if not Path(bin_path).exists():
            raise FileNotFoundError(f"binary not found: {bin_path}")

        self.spec = LaunchSpec(
            binary=Path(bin_path),
            start_url=start_url,
            extra_args=tuple(extra_args),
            headless=headless,
        )

        # --- per-instance filesystem --------------------------------------
        self._root = (root or state_root()) / "instances" / self.name
        self.fs = FilesystemInterface(self._root)

        # --- per-instance network -----------------------------------------
        self.net = NetworkInterface(blocklist=blocklist)

        # --- env ----------------------------------------------------------
        self._env_passthrough = tuple(env_passthrough)
        self._extra_env = dict(extra_env or {})

        self._proc: subprocess.Popen | None = None
        self._started_at: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ env
    def _build_env(self) -> dict[str, str]:
        keys = _BASE_ENV_KEYS_WIN if sys.platform.startswith("win") \
            else _BASE_ENV_KEYS_POSIX
        env: dict[str, str] = {}
        for k in keys:
            if k in os.environ:
                env[k] = os.environ[k]
        for k in self._env_passthrough:
            if k in os.environ:
                env[k] = os.environ[k]
        env.update(self._extra_env)
        # Tag the env so a child can detect it's running inside microvm.
        env["MICROVM_INSTANCE"] = self.name
        env["MICROVM_INSTANCE_ID"] = self.id
        env["MICROVM_ROOT"] = str(self._root)
        return env

    # ------------------------------------------------------------------ argv
    def _build_argv(self) -> list[str]:
        s = self.spec
        # These flags are Chromium-family contract. They're what makes the
        # per-instance isolation real.
        args = [
            str(s.binary),
            f"--user-data-dir={self.fs.profile_dir}",
            f"--proxy-server={self.net.proxy_url}",
            # Don't try to be the default browser, don't phone home on first run.
            "--no-default-browser-check",
            "--no-first-run",
            # Downloads go to fs.downloads_dir — that's handled by seeding
            # Preferences in _seed_preferences(); no CLI flag is needed.
        ]
        if s.headless:
            # --headless=new is the modern Chromium headless flag.
            args.append("--headless=new")
        args.extend(s.extra_args)
        args.append(s.start_url)
        return args

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "Instance":
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self  # already running

            self.net.start()

            # Seed the profile's Preferences so downloads land in our dir.
            # Chromium reads Default/Preferences on startup; if we create it
            # with download.default_directory set, new downloads go there.
            self._seed_preferences()

            argv = self._build_argv()
            env = self._build_env()

            # Windows: detach stdio to avoid inheriting console handles.
            # macOS/POSIX: start_new_session so Ctrl-C on the harness doesn't
            # cascade into the browser.
            kwargs: dict = {"env": env}
            if sys.platform.startswith("win"):
                DETACHED_PROCESS = 0x00000008
                kwargs["creationflags"] = DETACHED_PROCESS
                kwargs["close_fds"] = True
            else:
                kwargs["start_new_session"] = True
                kwargs["close_fds"] = True

            self._proc = subprocess.Popen(argv, **kwargs)
            self._started_at = time.time()
            return self

    def _seed_preferences(self) -> None:
        default_dir = self.fs.profile_dir / "Default"
        default_dir.mkdir(parents=True, exist_ok=True)
        prefs_path = default_dir / "Preferences"
        if prefs_path.exists():
            return
        prefs = {
            "download": {
                "default_directory": str(self.fs.downloads_dir),
                "prompt_for_download": False,
            },
            "profile": {"exit_type": "Normal"},
        }
        prefs_path.write_text(json.dumps(prefs), encoding="utf-8")

    def stop(self, timeout: float = 5.0) -> int | None:
        with self._lock:
            proc = self._proc
            if proc is None:
                self.net.stop()
                return None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
            rc = proc.returncode
            self._proc = None
            self.net.stop()
            return rc

    # ------------------------------------------------------------------ introspection
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def proxy_url(self) -> str:
        return self.net.proxy_url

    @property
    def root(self) -> Path:
        return self._root

    def info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "pid": self.pid,
            "running": self.running,
            "binary": str(self.spec.binary),
            "proxy": self.net.proxy_url,
            "root": str(self._root),
            "started_at": self._started_at,
        }

    # ------------------------------------------------------------------ context mgr
    def __enter__(self) -> "Instance":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
