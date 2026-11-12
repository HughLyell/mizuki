"""Filesystem locations used by Mizuki.

This module deliberately has no GTK dependencies so the source adapters and
storage helpers can be unit-tested on machines without a display.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import APP_NAME, __version__

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mizuki"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "mizuki"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mizuki"
THUMB_CACHE_DIR = CACHE_DIR / "thumbnails"


def _xdg_pictures() -> Path:
    """Return the user's Pictures directory, honouring xdg-user-dirs."""
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("XDG_PICTURES_DIR="):
                continue
            value = line.split("=", 1)[1].strip().strip('"')
            value = value.replace("$HOME", str(Path.home()))
            if value:
                return Path(value)
    except OSError:
        pass
    return Path.home() / "Pictures"


DEFAULT_DOWNLOAD_DIR = _xdg_pictures() / APP_NAME
SETTINGS_FILE = CONFIG_DIR / "settings.json"
FAVORITES_FILE = CONFIG_DIR / "favorites.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_app_dirs() -> None:
    for path in (CONFIG_DIR, DATA_DIR, CACHE_DIR, THUMB_CACHE_DIR, DEFAULT_DOWNLOAD_DIR):
        try:
            ensure_dir(path)
        except OSError:
            # A read-only home should not prevent the app from starting.
            pass


def resource_path(*parts: str) -> Path:
    """Locate a file shipped with Mizuki.

    Works from a git checkout (``data/`` next to ``src/``), from a Flatpak
    install (``/app/share/mizuki``), and from a traditional prefix install.
    ``MIZUKI_DATA_DIR`` overrides everything, which ``run.sh`` uses.
    """
    override = os.environ.get("MIZUKI_DATA_DIR")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    candidates.extend(
        [
            Path(__file__).resolve().parent.parent.parent / "data",
            Path("/app/share/mizuki"),
            Path("/usr/local/share/mizuki"),
            Path("/usr/share/mizuki"),
            Path.home() / ".local" / "share" / "mizuki" / "data",
        ]
    )
    for base in candidates:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    # Return the most useful guess even if it is missing so error messages make
    # sense.
    return candidates[0].joinpath(*parts) if candidates else Path(*parts)


USER_AGENT = (
    f"{APP_NAME}/{__version__} (Linux; GTK4) "
    f"(+https://github.com/HughLyell/mizuki)"
)
