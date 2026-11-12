"""Safebooru adapter (Gelbooru-style API at safebooru.org).

The Danbooru-hosted safebooru.donmai.us is behind a Cloudflare challenge that
blocks desktop API clients, so we use safebooru.org's long-standing JSON API
instead. It is safe-for-work only.
"""

from __future__ import annotations

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

    def search_tags(self, query: SearchQuery) -> list[str]:
        return [
            tag
            for tag in query.tags
            if not tag.lstrip("-").lower().startswith(("rating:", "order:", "status:"))
        ]

    def search(self, query: SearchQuery) -> SourcePage:
        limit = max(1, min(query.limit, 100))
        params = {
            "page": "dapi",
            "s": "post",
            "q": "index",
            "json": "1",
            "limit": limit,
            "pid": max(0, query.page - 1),
        }
        tags = self.search_tags(query)
        if tags:
            params["tags"] = " ".join(tags)
        try:
            payload = net.get_json(self.api_url, params=params, headers=self.headers())
        except net.NetworkError as exc:
            raise SourceError(str(exc)) from exc
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
        if query.sort == "score":
            posts.sort(key=lambda post: post.score, reverse=True)
        elif query.sort == "random":
            import random

            random.shuffle(posts)
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
