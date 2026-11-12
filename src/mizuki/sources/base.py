"""Base class for image sources.

A source is a small adapter that turns a :class:`SearchQuery` into a list of
:class:`Post` objects. Sources should never touch GTK: they are executed on
worker threads.
"""

from __future__ import annotations

from ..models import Post, SearchQuery, SourcePage


class SourceError(Exception):
    """An error with a message suitable for display to the user."""


class Source:
    """Base class for all sources.

    Subclasses fill in the metadata attributes and implement :meth:`search`.
    Plugin authors can subclass this and expose either a class or an instance
    named ``SOURCE`` in a ``.py`` file under ``~/.local/share/mizuki/sources``.
    """

    id: str = ""
    name: str = "Unknown source"
    description: str = ""
    homepage: str = ""
    icon: str = "image-x-generic-symbolic"
    supports_nsfw: bool = True
    max_tags: int = 0  # 0 means "no documented limit"
    popular_tags: tuple[str, ...] = ()

    def search(self, query: SearchQuery) -> SourcePage:  # pragma: no cover - interface
        raise NotImplementedError

    def suggest_tags(self, prefix: str, limit: int = 8) -> list[str]:
        """Return tag completions for ``prefix`` (optional)."""
        return []

    def headers(self) -> dict[str, str]:
        """Extra HTTP headers required by this source."""
        return {}

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Source {self.id}>"


def merge_pages(pages: list[SourcePage], *, limit: int) -> tuple[list[Post], bool]:
    """Round-robin merge results from several sources, removing duplicates."""
    seen: set[str] = set()
    queues = [list(page.posts) for page in pages if page.posts]
    merged: list[Post] = []
    while queues and len(merged) < limit:
        next_queues = []
        for queue in queues:
            if len(merged) >= limit:
                break
            while queue:
                post = queue.pop(0)
                if post.key in seen:
                    continue
                seen.add(post.key)
                merged.append(post)
                break
            if queue:
                next_queues.append(queue)
        queues = next_queues
    return merged, any(page.has_more for page in pages)
