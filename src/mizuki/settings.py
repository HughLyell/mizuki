"""JSON-backed application settings with change notifications."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "safe_search": True,
    "blur_nsfw": True,
    "sidebar_visible": True,
    "source": "danbooru",
    "enabled_sources": ["danbooru", "safebooru", "konachan", "yandere", "zerochan"],
    "sort": "newest",
    "grid_size": "comfortable",
    "accent": "mauve",
    "color_scheme": "system",
    "download_dir": str(config.DEFAULT_DOWNLOAD_DIR),
    "recent_searches": [],
    "source_filters": {},
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
        # Deep copy: the mutable defaults (lists/dicts) must never be shared
        # with the module-level DEFAULTS.
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
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
        for key in DEFAULTS:
            self._validate_key(key)

    def _validate_key(self, key: str) -> None:
        data = self._data
        if key in ("safe_search", "blur_nsfw", "sidebar_visible"):
            data[key] = bool(data.get(key, DEFAULTS[key]))
        elif key == "grid_size":
            if data.get(key) not in VALID_GRID_SIZES:
                data[key] = DEFAULTS[key]
        elif key == "sort":
            if data.get(key) not in VALID_SORTS:
                data[key] = DEFAULTS[key]
        elif key == "color_scheme":
            if data.get(key) not in VALID_SCHEMES:
                data[key] = DEFAULTS[key]
        elif key == "accent":
            if data.get(key) not in VALID_ACCENTS:
                data[key] = DEFAULTS[key]
        elif key == "source":
            if not isinstance(data.get(key), str):
                data[key] = DEFAULTS[key]
        elif key == "enabled_sources":
            sources = data.get(key)
            if not isinstance(sources, list) or not sources:
                data[key] = list(DEFAULTS[key])
            else:
                data[key] = [str(item) for item in sources]
        elif key == "recent_searches":
            recent = data.get(key)
            if not isinstance(recent, list):
                data[key] = []
            else:
                data[key] = [str(item) for item in recent if str(item).strip()][:12]
        elif key == "download_dir":
            download_dir = data.get(key)
            if not isinstance(download_dir, str) or not download_dir.strip():
                data[key] = DEFAULTS[key]
        elif key == "source_filters":
            source_filters = data.get(key)
            if not isinstance(source_filters, dict):
                data[key] = {}
            else:
                data[key] = {
                    str(source_id): str(value)[:500]
                    for source_id, value in source_filters.items()
                    if isinstance(source_id, str) and isinstance(value, str) and value.strip()
                }

    # -- access ----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Unknown setting: {key}")
        previous = self._data.get(key)
        if previous == value:
            return
        self._data[key] = value
        # Validate on write so an invalid value can never be stored or saved.
        self._validate_key(key)
        new_value = self._data[key]
        if new_value == previous:
            return
        if save:
            self.save()
        self._notify(key, new_value)

    def update(self, values: dict[str, Any]) -> None:
        changed = False
        for key, value in values.items():
            if key not in DEFAULTS:
                raise KeyError(f"Unknown setting: {key}")
            if self._data.get(key) != value:
                self._data[key] = value
                self._validate_key(key)
                changed = True
                self._notify(key, self._data[key])
        if changed:
            self.save()

    def subscribe(self, key: str, callback: Listener) -> None:
        if callback not in self._listeners[key]:
            self._listeners[key].append(callback)

    def unsubscribe(self, key: str, callback: Listener) -> None:
        listeners = self._listeners.get(key)
        if listeners and callback in listeners:
            listeners.remove(callback)

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

    def remove_recent_search(self, text: str) -> None:
        text = " ".join(str(text or "").split())
        if not text:
            return
        recent = [
            item for item in self.get("recent_searches", [])
            if item.lower() != text.lower()
        ]
        self.set("recent_searches", recent)

    def get_source_filter(self, source_id: str) -> str:
        filters = self.get("source_filters", {})
        if not isinstance(filters, dict):
            return ""
        return str(filters.get(source_id, ""))

    def set_source_filter(self, source_id: str, value: str) -> None:
        filters = dict(self.get("source_filters", {}))
        value = str(value or "").strip()[:500]
        if value:
            filters[source_id] = value
        else:
            filters.pop(source_id, None)
        self.set("source_filters", filters)

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
