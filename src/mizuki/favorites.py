"""Persistent favourites store."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from . import config
from .models import Favorite, Post

log = logging.getLogger(__name__)


class FavoriteStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else config.FAVORITES_FILE
        self._items: dict[str, Favorite] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read favourites: %s", exc)
            return
        if not isinstance(raw, list):
            return
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            data = entry.get("post")
            if not isinstance(data, dict):
                continue
            try:
                post = Post.from_dict(data)
            except (TypeError, ValueError):
                continue
            if not post.key or post.key == ":":
                continue
            self._items[post.key] = Favorite(post=post, added=str(entry.get("added", "")))

    def save(self) -> None:
        payload = [
            {"added": favorite.added, "post": favorite.post.to_dict()}
            for favorite in self._items.values()
        ]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix="favorites-", suffix=".json", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        except OSError as exc:
            log.warning("Could not save favourites: %s", exc)

    def all(self) -> list[Post]:
        return [favorite.post for favorite in reversed(list(self._items.values()))]

    def count(self) -> int:
        return len(self._items)

    def contains(self, post: Post) -> bool:
        return post.key in self._items

    def toggle(self, post: Post) -> bool:
        """Toggle a post and return ``True`` if it is now a favourite."""
        if post.key in self._items:
            del self._items[post.key]
            self.save()
            return False
        self._items[post.key] = Favorite(post=post)
        self.save()
        return True

    def remove(self, post: Post) -> None:
        if self._items.pop(post.key, None) is not None:
            self.save()

    def clear(self) -> None:
        if self._items:
            self._items.clear()
            self.save()
