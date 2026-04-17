"""Platform helpers.

Kept deliberately tiny: we only need to (a) locate a default Chromium binary
if the caller doesn't provide one, and (b) know where to put per-instance
state on disk. Everything else is OS-agnostic.
"""
from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def state_root() -> Path:
    """Where per-instance directories live.

    Uses MICROVM_HOME if set, else a platform-appropriate user dir.
    """
    override = os.environ.get("MICROVM_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "microvm"
    if is_macos():
        return Path.home() / "Library" / "Application Support" / "microvm"
    return Path.home() / ".local" / "share" / "microvm"


# Common install locations. Order matters: first hit wins.
_DEFAULT_CHROMIUM_PATHS = {
    "darwin": [
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "win32": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
}


def find_default_browser() -> Path | None:
    """Best-effort lookup of an installed Chromium-family binary.

    Returns None if nothing is found; callers should then require an explicit
    binary path. We do not guess on Linux here — this package targets
    Windows and macOS per the project brief.
    """
    candidates = _DEFAULT_CHROMIUM_PATHS.get(sys.platform, [])
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    # Fall back to PATH as a last resort.
    for name in ("chromium", "chrome", "google-chrome", "msedge", "brave"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None
