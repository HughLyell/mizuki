"""Asynchronous thumbnail loading with a small memory and disk cache."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

from .. import config, net  # noqa: E402

log = logging.getLogger(__name__)

TextureCallback = Callable[[object | None], None]

MAX_THUMB_BYTES = 400 * 1024 * 1024  # thumbnail cache ceiling


class ThumbnailLoader:
    """Load remote images off the main thread and return ``Gdk.Texture`` objects.

    Textures are cached in an LRU map. Disk caching avoids re-downloading
    thumbnails when browsing the same tags again.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        max_textures: int = 240,
        workers: int = 6,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else config.THUMB_CACHE_DIR
        self.max_textures = max_textures
        self._memory: "OrderedDict[str, Gdk.Texture]" = OrderedDict()
        self._inflight: dict[str, list[TextureCallback]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mizuki-image")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("Thumbnail cache directory is unavailable: %s", self.cache_dir)

    # -- public API ------------------------------------------------------
    def cached(self, url: str):
        with self._lock:
            texture = self._memory.get(url)
            if texture is not None:
                self._memory.move_to_end(url)
            return texture

    def load(
        self,
        url: str,
        callback: TextureCallback,
        *,
        referer: str = "",
        store_disk: bool = True,
    ) -> None:
        if not url:
            GLib.idle_add(callback, None)
            return
        texture = self.cached(url)
        if texture is not None:
            callback(texture)
            return
        with self._lock:
            waiting = self._inflight.get(url)
            if waiting is not None:
                waiting.append(callback)
                return
            self._inflight[url] = [callback]
        self._executor.submit(self._fetch, url, referer, store_disk)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # -- worker ----------------------------------------------------------
    def _fetch(self, url: str, referer: str, store_disk: bool) -> None:
        data: bytes | None = None
        cache_path = self._cache_path(url)
        if store_disk:
            try:
                data = cache_path.read_bytes()
            except OSError:
                data = None
        if data is None:
            headers = {"Referer": referer} if referer else None
            try:
                data = net.get_bytes(url, headers=headers, timeout=25)
                if store_disk and data:
                    try:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = cache_path.with_suffix(cache_path.suffix + ".part")
                        temporary.write_bytes(data)
                        os.replace(temporary, cache_path)
                    except OSError:
                        pass
            except net.NetworkError as exc:
                log.info("Thumbnail failed (%s): %s", url, exc)
                data = None
            except Exception:
                log.exception("Unexpected thumbnail error")
                data = None
        GLib.idle_add(self._deliver, url, data)

    def _deliver(self, url: str, data: bytes | None):
        texture = None
        if data:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except Exception:
                texture = None
        if texture is not None:
            with self._lock:
                self._memory[url] = texture
                self._memory.move_to_end(url)
                while len(self._memory) > self.max_textures:
                    self._memory.popitem(last=False)
        with self._lock:
            callbacks = self._inflight.pop(url, [])
        for callback in callbacks:
            try:
                callback(texture)
            except Exception:
                log.exception("Thumbnail callback failed")
        return False

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.img"

    # -- maintenance -----------------------------------------------------
    def prune_disk_cache(self, max_bytes: int = MAX_THUMB_BYTES) -> int:
        """Delete the oldest cached thumbnails if the cache exceeds ``max_bytes``."""
        files: list[tuple[float, int, Path]] = []
        total = 0
        try:
            for path in self.cache_dir.rglob("*.img"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((stat.st_mtime, stat.st_size, path))
                total += stat.st_size
        except OSError:
            return 0
        if total <= max_bytes:
            return 0
        removed = 0
        for _, size, path in sorted(files):
            if total <= max_bytes:
                break
            try:
                path.unlink()
                total -= size
                removed += 1
            except OSError:
                continue
        return removed

    def start_maintenance(self) -> None:
        threading.Thread(target=self.prune_disk_cache, daemon=True, name="mizuki-cache-prune").start()
