"""Base class for image sources.

A source is a small adapter that turns a :class:`SearchQuery` into a list of
:class:`Post` objects. Sources should never touch GTK: they are executed on
worker threads.
"""

from __future__ import annotations

from ..models import Post, SearchQuery, SourcePage, parse_tags, split_comma_groups


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

    def query_tags(self, query: SearchQuery) -> list[str]:
        """Return the source-specific tags for a query.

        Most boorus separate tags with whitespace and use underscores inside a
        tag. Commas are treated as explicit groups: spaces inside a comma group
        are converted to underscores, while whitespace-only text keeps the
        normal multi-tag behaviour.
        """
        groups = split_comma_groups(query.text)
        if groups is None:
            return query.tags
        tags: list[str] = []
        for group in groups:
            tag = "_".join(group.split())
            if tag and tag not in tags:
                tags.append(tag)
        return tags

    def display_tags(self, text: str) -> list[str]:
        """Split raw query text for display and chip removal."""
        groups = split_comma_groups(text)
        return groups if groups is not None else parse_tags(text)

    def join_tags(self, tags: list[str]) -> str:
        """Join chips back into the search box syntax for this source."""
        return ", ".join(tags)

    def fallback_phrase_tags(self, query: SearchQuery) -> list[str] | None:
        """Return a single underscored phrase for multiword tag input.

        Booru users often type a tag name with spaces even though boorus store
        underscores. This fallback is used only when the normal multi-tag
        interpretation returns nothing (or is too large for the source).
        """
        raw = str(query.text or "").strip().strip('"').strip()
        if not raw or "," in raw:
            return None
        words = raw.split()
        if len(words) < 2:
            return None
        if any(word.startswith("-") or ":" in word or word in {"~", "or"} for word in words):
            return None
        phrase = "_".join(words)
        current = [tag.lower().replace("_", " ") for tag in self.query_tags(query)]
        if len(current) == 1 and current[0] == phrase.lower().replace("_", " "):
            return None
        return [phrase]

    def search_variants(self, query: SearchQuery) -> list[list[str]]:
        """Return tag variants to try, best interpretation first."""
        normal = self.query_tags(query)
        phrase = self.fallback_phrase_tags(query)
        if not phrase:
            return [normal]
        raw_words = [
            word for word in str(query.text or "").strip().strip('"').split()
            if word
        ]
        if self.max_tags and len(normal) > self.max_tags:
            variants = [phrase, normal]
        elif len(raw_words) == 2:
            variants = [phrase, normal]
        else:
            variants = [normal, phrase]
        cached = getattr(self, "_variant_cache", {}).get(self._variant_cache_key(query))
        if cached:
            variants = [cached] + [variant for variant in variants if variant != cached]
        return variants

    @staticmethod
    def _variant_cache_key(query: SearchQuery) -> str:
        return " ".join(str(query.text or "").lower().split())

    def remember_variant(self, query: SearchQuery, tags: list[str]) -> None:
        """Remember the interpretation that actually produced results."""
        if not tags:
            return
        if not hasattr(self, "_variant_cache"):
            self._variant_cache: dict[str, list[str]] = {}
        self._variant_cache[self._variant_cache_key(query)] = list(tags)

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
