"""Danbooru and Safebooru (Danbooru 2.x API)."""

from __future__ import annotations

from .. import net
from ..models import IMAGE_EXTENSIONS, Post, SearchQuery, SourcePage, normalize_rating
from .base import Source, SourceError


class DanbooruSource(Source):
    id = "danbooru"
    name = "Danbooru"
    description = "Huge art booru with detailed tags and artist metadata."
    homepage = "https://danbooru.donmai.us"
    icon = "image-x-generic-symbolic"
    supports_nsfw = True
    max_tags = 2  # anonymous API limit
    popular_tags = (
        "1girl",
        "landscape",
        "scenery",
        "cityscape",
        "sky",
        "flowers",
        "portrait",
        "kimono",
    )

    base_url = "https://danbooru.donmai.us"
    always_safe = False

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Referer": self.base_url + "/"}

    def search_tags(self, query: SearchQuery) -> list[str]:
        tags = [
            tag
            for tag in query.tags
            if not tag.lstrip("-").lower().startswith(("rating:", "order:", "status:", "score:"))
        ]
        if self.always_safe or query.safe:
            tags.append("rating:g")
        if query.sort == "score":
            tags.append("order:score")
        elif query.sort == "random":
            tags.append("order:random")
        elif query.sort == "relevance":
            tags.append("order:rank")
        return tags

    def search(self, query: SearchQuery) -> SourcePage:
        if len(query.tags) > 2:
            raise SourceError(
                "Danbooru's anonymous API allows at most two tags per search. "
                "Try fewer tags, or use another source."
            )
        limit = max(1, min(query.limit, 100))
        tags = self.search_tags(query)
        try:
            payload = net.get_json(
                self.base_url + "/posts.json",
                params={"tags": " ".join(tags), "limit": limit, "page": query.page},
                headers=self.headers(),
            )
        except net.NetworkError as exc:
            raise SourceError(str(exc)) from exc
        if not isinstance(payload, list):
            raise SourceError("The source returned an unexpected response.")
        posts: list[Post] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            post = self._parse(raw)
            if post is None:
                continue
            if query.safe and not post.is_safe:
                continue
            posts.append(post)
        return SourcePage(posts=posts, has_more=len(payload) >= limit)

    def _parse(self, raw: dict) -> Post | None:
        ext = str(raw.get("file_ext") or "").lower()
        if ext not in IMAGE_EXTENSIONS:
            return None
        file_url = raw.get("file_url")
        if not file_url:
            return None
        post_id = str(raw.get("id") or "")
        if not post_id:
            return None
        thumb = raw.get("preview_file_url") or raw.get("large_file_url") or file_url
        preview = raw.get("large_file_url") or file_url
        tag_string = str(raw.get("tag_string") or "")
        tags = tuple(tag_string.split()[:80])
        artist_tags = str(raw.get("tag_string_artist") or "").split()
        artist = artist_tags[0].replace("_", " ") if artist_tags else ""
        return Post(
            id=post_id,
            source=self.id,
            source_name=self.name,
            thumb_url=str(thumb),
            preview_url=str(preview),
            file_url=str(file_url),
            page_url=f"{self.base_url}/posts/{post_id}",
            width=int(raw.get("image_width") or 0),
            height=int(raw.get("image_height") or 0),
            tags=tags,
            rating=normalize_rating(raw.get("rating")),
            artist=artist,
            ext="jpg" if ext == "jpeg" else ext,
            score=int(raw.get("score") or 0),
            referer=self.base_url + "/",
        )

    def suggest_tags(self, prefix: str, limit: int = 8) -> list[str]:
        prefix = prefix.strip().replace(" ", "_")
        if len(prefix) < 2:
            return []
        try:
            payload = net.get_json(
                self.base_url + "/tags.json",
                params={
                    "search[name_matches]": f"{prefix}*",
                    "search[order]": "count",
                    "limit": limit,
                },
                headers=self.headers(),
                timeout=8,
            )
        except net.NetworkError:
            return []
        if not isinstance(payload, list):
            return []
        names = [str(item.get("name", "")) for item in payload if isinstance(item, dict)]
        return [name for name in names if name][:limit]

