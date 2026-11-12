"""JSON-backed application settings with change notifications."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from . import config

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "safe_search": True,
    "blur_nsfw": True,
    "source": "danbooru",
    "enabled_sources": ["danbooru", "safebooru", "konachan", "yandere", "zerochan"],
    "sort": "newest",
    "grid_size": "comfortable",
    "accent": "mauve",
    "color_scheme": "system",
    "download_dir": str(config.DEFAULT_DOWNLOAD_DIR),
    "recent_searches": [],
}

VALID_GRID_SIZES = {"compact", "comfortable", "large"}
VALID_SORTS = {"newest", "score", "relevance", "random"}
VALID_SCHEMES = {"system", "light", "dark"}
VALID_ACCENTS = {"mauve", "blue", "green", "peach", "rose"}

Listener = Callable[[str, Any], None]


class Settings:
    """Tiny observable settings store.

    Settings are saved immediately. The file is written atomically so a crash
    can never leave a truncated settings file behind.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config.SETTINGS_FILE
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._listeners: dict[str, list[Listener]] = defaultdict(list)
        self.load()

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read settings: %s", exc)
            return
        if not isinstance(raw, dict):
            return
        for key in DEFAULTS:
            if key in raw:
                self._data[key] = raw[key]
        self._validate()

    def _validate(self) -> None:
        data = self._data
        for key in ("safe_search", "blur_nsfw"):
            data[key] = bool(data.get(key, DEFAULTS[key]))
        if data.get("grid_size") not in VALID_GRID_SIZES:
            data["grid_size"] = DEFAULTS["grid_size"]
        if data.get("sort") not in VALID_SORTS:
            data["sort"] = DEFAULTS["sort"]
        if data.get("color_scheme") not in VALID_SCHEMES:
            data["color_scheme"] = DEFAULTS["color_scheme"]
        if data.get("accent") not in VALID_ACCENTS:
            data["accent"] = DEFAULTS["accent"]
        if not isinstance(data.get("source"), str):
            data["source"] = DEFAULTS["source"]
        sources = data.get("enabled_sources")
        if not isinstance(sources, list) or not sources:
            data["enabled_sources"] = list(DEFAULTS["enabled_sources"])
        else:
            data["enabled_sources"] = [str(item) for item in sources]
        recent = data.get("recent_searches")
        if not isinstance(recent, list):
            data["recent_searches"] = []
        else:
            data["recent_searches"] = [str(item) for item in recent if str(item).strip()][:12]
        download_dir = data.get("download_dir")
        if not isinstance(download_dir, str) or not download_dir.strip():
            data["download_dir"] = DEFAULTS["download_dir"]

    # -- access ----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        if self._data.get(key) == value:
            return
        self._data[key] = value
        if save:
            self.save()
        self._notify(key, value)

    def update(self, values: dict[str, Any]) -> None:
        changed = False
        for key, value in values.items():
            if key not in DEFAULTS:
                raise KeyError(f"Unknown setting: {key}")
            if self._data.get(key) != value:
                self._data[key] = value
                changed = True
                self._notify(key, value)
        if changed:
            self.save()

    def subscribe(self, key: str, callback: Listener) -> None:
        if callback not in self._listeners[key]:
            self._listeners[key].append(callback)

    def _notify(self, key: str, value: Any) -> None:
        for callback in tuple(self._listeners.get(key, ())):
            try:
                callback(key, value)
            except Exception:  # pragma: no cover - defensive UI boundary
                log.exception("Settings listener failed for %s", key)

    # -- convenience -----------------------------------------------------
    def add_recent_search(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        recent = [item for item in self.get("recent_searches", []) if item.lower() != text.lower()]
        recent.insert(0, text)
        self.set("recent_searches", recent[:12])

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)
