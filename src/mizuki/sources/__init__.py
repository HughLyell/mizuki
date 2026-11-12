"""Source registry and third-party source loading."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections import OrderedDict
from pathlib import Path

from .. import config
from .base import Source, SourceError, merge_pages

log = logging.getLogger(__name__)

__all__ = ["Source", "SourceError", "SourceRegistry", "merge_pages"]

_BUILTINS: "OrderedDict[str, tuple[str, str]]" = OrderedDict(
    [
        ("danbooru", ("mizuki.sources.danbooru", "DanbooruSource")),
        ("safebooru", ("mizuki.sources.safebooru", "SafebooruSource")),
        ("konachan", ("mizuki.sources.moebooru", "KonachanSource")),
        ("yandere", ("mizuki.sources.moebooru", "YandeSource")),
        ("zerochan", ("mizuki.sources.zerochan", "ZerochanSource")),
    ]
)


class SourceRegistry:
    """Lazily instantiate built-in sources and discover user plugins."""

    def __init__(self, *, include_plugins: bool = True) -> None:
        self._sources: "OrderedDict[str, Source]" = OrderedDict()
        self._plugins: list[str] = []
        self.load_builtins()
        if include_plugins:
            self.load_plugins()

    def load_builtins(self) -> None:
        for source_id, (module_name, class_name) in _BUILTINS.items():
            try:
                module = importlib.import_module(module_name)
                source_class = getattr(module, class_name)
                self._sources[source_id] = source_class()
            except Exception:
                log.exception("Could not load built-in source %s", source_id)

    def load_plugins(self, directory: Path | None = None) -> None:
        directory = directory or (config.DATA_DIR / "sources")
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            source = self._load_plugin_file(path)
            if source is None:
                continue
            if not source.id:
                log.warning("Plugin %s has no id; skipping", path.name)
                continue
            if source.id in self._sources:
                log.warning("Plugin %s reuses source id %s; skipping", path.name, source.id)
                continue
            self._sources[source.id] = source
            self._plugins.append(path.name)

    def _load_plugin_file(self, path: Path) -> Source | None:
        module_name = f"mizuki_user_source_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            candidate = getattr(module, "SOURCE", None)
            if candidate is None:
                for value in vars(module).values():
                    if (
                        isinstance(value, type)
                        and issubclass(value, Source)
                        and value is not Source
                    ):
                        candidate = value
                        break
            if isinstance(candidate, type) and issubclass(candidate, Source):
                candidate = candidate()
            if not isinstance(candidate, Source):
                log.warning("Plugin %s does not expose a Source instance or class", path.name)
                return None
            return candidate
        except Exception:
            log.exception("Could not load user source %s", path.name)
            return None

    # -- access ----------------------------------------------------------
    def all(self) -> list[Source]:
        return list(self._sources.values())

    def ids(self) -> list[str]:
        return list(self._sources.keys())

    def get(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    def select(self, source_ids: list[str] | tuple[str, ...] | None) -> list[Source]:
        if not source_ids:
            return self.all()
        seen: set[str] = set()
        selected: list[Source] = []
        for source_id in source_ids:
            source = self._sources.get(source_id)
            if source is not None and source.id not in seen:
                selected.append(source)
                seen.add(source.id)
        return selected

    @property
    def plugin_names(self) -> list[str]:
        return list(self._plugins)

    @property
    def plugin_directory(self) -> Path:
        return config.DATA_DIR / "sources"
