"""Konachan and Yande.re (Moebooru API)."""

from __future__ import annotations

from .. import net
from ..models import IMAGE_EXTENSIONS, Post, SearchQuery, SourcePage, extension_from_url
from .base import Source, SourceError


def moebooru_rating(raw: object) -> str:
    """Moebooru uses s/q/e where ``s`` means safe (not sensitive)."""
    value = str(raw or "").strip().lower()
    return {
        "s": "safe",
        "safe": "safe",
        "q": "questionable",
        "questionable": "questionable",
        "e": "explicit",
        "explicit": "explicit",
    }.get(value, "safe")


class MoebooruSource(Source):
    supports_nsfw = True
    icon = "emblem-photos-symbolic"
    max_tags = 6

    base_url = ""
    safe_rating = "rating:safe"

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Referer": self.base_url + "/"}

    def search_tags(self, query: SearchQuery, tags_override: list[str] | None = None) -> list[str]:
        tags = [
            tag
            for tag in (tags_override if tags_override is not None else self.query_tags(query))
            if not tag.lstrip("-").lower().startswith(("rating:", "order:", "status:"))
        ]
        if query.safe:
            tags.append(self.safe_rating)
        if query.sort == "score":
            tags.append("order:score")
        elif query.sort == "random":
            tags.append("order:random")
        return tags

    def search(self, query: SearchQuery) -> SourcePage:
        last_page: SourcePage | None = None
        last_error: SourceError | None = None
        for tags in self.search_variants(query):
            if self.max_tags and len(tags) > self.max_tags:
                continue
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
        raise SourceError(f"{self.name} allows at most 6 tags per search.")

    def _search_tags(self, query: SearchQuery, tags_override: list[str]) -> SourcePage:
        limit = max(1, min(query.limit, 100))
        try:
            payload = net.get_json(
                self.base_url + "/post.json",
                params={
                    "tags": " ".join(self.search_tags(query, tags_override)),
                    "limit": limit,
                    "page": query.page,
                },
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
        file_url = str(raw.get("file_url") or "")
        if not file_url:
            return None
        ext = extension_from_url(file_url, fallback=str(raw.get("file_ext") or "jpg").lower())
        if ext not in IMAGE_EXTENSIONS:
            return None
        post_id = str(raw.get("id") or "")
        if not post_id:
            return None
        preview = raw.get("sample_url") or raw.get("jpeg_url") or file_url
        thumb = raw.get("preview_url") or preview
        tags = tuple(str(raw.get("tags") or "").split()[:80])
        return Post(
            id=post_id,
            source=self.id,
            source_name=self.name,
            thumb_url=str(thumb),
            preview_url=str(preview),
            file_url=file_url,
            page_url=f"{self.base_url}/post/show/{post_id}",
            width=int(raw.get("width") or raw.get("sample_width") or 0),
            height=int(raw.get("height") or raw.get("sample_height") or 0),
            tags=tags,
            rating=moebooru_rating(raw.get("rating")),
            ext=ext,
            score=int(raw.get("score") or 0),
            referer=self.base_url + "/",
        )


class KonachanSource(MoebooruSource):
    id = "konachan"
    name = "Konachan"
    description = "High-resolution anime wallpapers and illustrations."
    homepage = "https://konachan.com"
    icon = "preferences-desktop-wallpaper-symbolic"
    popular_tags = (
        "landscape",
        "scenery",
        "sky",
        "clouds",
        "city",
        "sunset",
        "1girl",
        "original",
    )
    base_url = "https://konachan.com"


class YandeSource(MoebooruSource):
    id = "yandere"
    name = "Yande.re"
    description = "Long-running booru known for high-quality scans and art."
    homepage = "https://yande.re"
    icon = "emblem-favorite-symbolic"
    popular_tags = (
        "landscape",
        "scenery",
        "sky",
        "clouds",
        "city",
        "sunset",
        "1girl",
        "original",
    )
    base_url = "https://yande.re"
