"""Safebooru adapter (Gelbooru-style API at safebooru.org).

The Danbooru-hosted safebooru.donmai.us is behind a Cloudflare challenge that
blocks desktop API clients, so we use safebooru.org's long-standing JSON API
instead. It is safe-for-work only.
"""

from __future__ import annotations

import json

from .. import net
from ..models import IMAGE_EXTENSIONS, Post, SearchQuery, SourcePage, extension_from_url, normalize_rating
from .base import Source, SourceError


class SafebooruSource(Source):
    id = "safebooru"
    name = "Safebooru"
    description = "Large safe-for-work booru with a lightweight Gelbooru API."
    homepage = "https://safebooru.org"
    icon = "security-high-symbolic"
    supports_nsfw = False
    max_tags = 0
    popular_tags = (
        "1girl",
        "landscape",
        "scenery",
        "sky",
        "city",
        "flowers",
        "smile",
        "portrait",
    )

    api_url = "https://safebooru.org/index.php"

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Referer": "https://safebooru.org/"}

    def search_tags(self, query: SearchQuery, tags_override: list[str] | None = None) -> list[str]:
        tags = [
            tag
            for tag in (tags_override if tags_override is not None else self.query_tags(query))
            if not tag.lstrip("-").lower().startswith(("rating:", "order:", "status:"))
        ]
        if query.sort == "score":
            tags.append("sort:score:desc")
        elif query.sort == "random":
            tags.append("sort:random")
        return tags

    def search(self, query: SearchQuery) -> SourcePage:
        last_page: SourcePage | None = None
        last_error: SourceError | None = None
        for tags in self.search_variants(query):
            try:
                page = self._search_tags(query, tags)
            except SourceError as exc:
                last_error = exc
                continue
            last_page = page
            if page.posts:
                self.remember_variant(query, tags)
                return page
        if last_page is not None:
            return last_page
        if last_error is not None:
            raise last_error
        return SourcePage(posts=[], has_more=False)

    def _search_tags(self, query: SearchQuery, tags_override: list[str]) -> SourcePage:
        limit = max(1, min(query.limit, 100))
        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "limit": limit,
            "pid": max(0, query.page - 1),
        }
        tags = self.search_tags(query, tags_override=tags_override)
        if tags:
            params["tags"] = " ".join(tags)
        try:
            raw_text = net.get_text(self.api_url, params=params, headers=self.headers())
        except net.NetworkError as exc:
            raise SourceError(str(exc)) from exc
        if not raw_text.strip():
            return SourcePage(posts=[], has_more=False)
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            preview = " ".join(raw_text[:120].split())
            raise SourceError(f"Safebooru returned an unexpected response ({preview!r})") from exc
        if isinstance(payload, dict):
            payload = payload.get("post") or []
        if not isinstance(payload, list):
            raise SourceError("Safebooru returned an unexpected response.")
        posts: list[Post] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            post = self._parse(raw)
            if post is not None and post.is_safe:
                posts.append(post)
        return SourcePage(posts=posts, has_more=len(payload) >= limit)

    def _parse(self, raw: dict) -> Post | None:
        file_url = str(raw.get("file_url") or raw.get("sample_url") or "")
        if not file_url:
            return None
        ext = extension_from_url(file_url, fallback=str(raw.get("image") or "jpg").rsplit(".", 1)[-1])
        if ext not in IMAGE_EXTENSIONS:
            return None
        post_id = str(raw.get("id") or "")
        if not post_id:
            return None
        preview = str(raw.get("sample_url") or file_url)
        thumb = str(raw.get("preview_url") or preview)
        tags = tuple(str(raw.get("tags") or "").split()[:80])
        try:
            score = int(raw.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return Post(
            id=post_id,
            source=self.id,
            source_name=self.name,
            thumb_url=thumb,
            preview_url=preview,
            file_url=file_url,
            page_url=f"https://safebooru.org/index.php?page=post&s=view&id={post_id}",
            width=int(raw.get("width") or 0),
            height=int(raw.get("height") or 0),
            tags=tags,
            rating=normalize_rating(raw.get("rating")),
            ext=ext,
            score=score,
            referer="https://safebooru.org/",
        )
