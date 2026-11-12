"""Zerochan adapter.

Zerochan has no dependable public JSON API (the ``?json`` endpoint is
intermittent), but its gallery pages are richly structured: every tile carries
the post id, thumbnail, dimensions, artist and a direct full-size image link.
We parse those tiles with the standard library's HTML parser and degrade
gracefully if the markup changes.
"""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

from .. import net
from ..models import Post, SearchQuery, SourcePage, extension_from_url
from .base import Source, SourceError

BASE_URL = "https://www.zerochan.net"
STATIC_URL = "https://static.zerochan.net"

_IGNORED_PATHS = {
    "",
    "login",
    "register",
    "search",
    "tags",
    "api",
    "faq",
    "upload",
    "settings",
    "fav",
    "full",
    "random",
    "about",
}

_DIMENSIONS = re.compile(r"(\d+)\s*[×✕x]\s*(\d+)")


def _jpg_thumbnail(url: str) -> str:
    """Prefer JPEG tiles for maximum gdk-pixbuf compatibility."""
    if not url:
        return url
    return re.sub(r"\.(?:avif|webp)(?:\?.*)?$", ".jpg", url)


class _GalleryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._current: dict | None = None
        self._anchor: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "li":
            post_id = data.get("data-id")
            if post_id and post_id.isdigit() and self._current is None:
                self._current = {
                    "id": post_id,
                    "thumb": "",
                    "full": "",
                    "page": "",
                    "tags": [],
                    "title": "",
                    "alt": "",
                    "width": 0,
                    "height": 0,
                }
            return
        if self._current is None:
            return
        if tag == "img":
            src = data.get("data-src") or data.get("src") or ""
            if src and not self._current["thumb"]:
                self._current["thumb"] = urllib.parse.urljoin(BASE_URL, src)
            self._current["title"] = data.get("title") or self._current["title"]
            self._current["alt"] = data.get("alt") or self._current["alt"]
            try:
                self._current["width"] = int(data.get("width") or 0)
                self._current["height"] = int(data.get("height") or 0)
            except (TypeError, ValueError):
                pass
        elif tag == "a":
            href = data.get("href") or ""
            if not href:
                return
            absolute = urllib.parse.urljoin(BASE_URL, href)
            if "static.zerochan.net" in absolute and re.search(r"\.full\.\d+\.", absolute):
                self._current["full"] = absolute
            elif re.fullmatch(r"/\d+", href):
                self._current["page"] = absolute
            elif href.startswith("/") and not href.startswith("//"):
                path = urllib.parse.unquote(href.strip("/")).replace("+", " ")
                head = path.split("/", 1)[0].lower()
                if head not in _IGNORED_PATHS and not path.isdigit():
                    self._anchor = {"href": href, "text": []}
        elif tag == "p" and self._anchor is not None:
            # The tag list is a <p> of anchors; nothing special needed.
            pass

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None and self._current is not None:
            href = self._anchor["href"]
            text = " ".join("".join(self._anchor["text"]).split())
            slug = urllib.parse.unquote(href.strip("/")).replace("+", " ").strip().lower()
            if slug and slug not in _IGNORED_PATHS and not slug.isdigit():
                if slug not in self._current["tags"]:
                    self._current["tags"].append(slug)
            if text and text.lower() not in _IGNORED_PATHS and len(text) < 80:
                display = text.lower()
                if display not in self._current["tags"]:
                    self._current["tags"].append(display)
            self._anchor = None
        elif tag == "li" and self._current is not None:
            self._finish_current()

    def _finish_current(self) -> None:
        current = self._current
        self._current = None
        self._anchor = None
        if not current or not current["thumb"]:
            return
        if not current["full"]:
            current["full"] = current["thumb"]
        self.items.append(current)


class ZerochanSource(Source):
    id = "zerochan"
    name = "Zerochan"
    description = "Curated anime artwork and wallpapers. Safe-for-work only."
    homepage = BASE_URL
    icon = "starred-symbolic"
    supports_nsfw = False
    max_tags = 0
    popular_tags = (
        "landscape",
        "scenery",
        "anime",
        "sky",
        "sunset",
        "city",
        "flowers",
        "1girl",
    )

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "Referer": BASE_URL + "/",
        }

    def search_path(self, query: SearchQuery) -> str:
        tags = query.tags[:3]
        if not tags:
            return "/"
        encoded = ",".join(urllib.parse.quote(tag.replace(" ", "+"), safe="+()_.-") for tag in tags)
        return "/" + encoded

    def search_url(self, query: SearchQuery) -> str:
        path = self.search_path(query)
        params: list[str] = []
        if query.page > 1:
            params.append(f"p={query.page}")
        if query.sort == "score":
            params.extend(["s=fav", "t=0"])
        if params:
            return f"{BASE_URL}{path}?{'&'.join(params)}"
        return f"{BASE_URL}{path}"

    def search(self, query: SearchQuery) -> SourcePage:
        try:
            html = net.get_text(self.search_url(query), headers=self.headers(), timeout=25)
        except net.NetworkError as exc:
            raise SourceError(str(exc)) from exc
        if "Just a moment" in html and "cloudflare" in html.lower():
            raise SourceError("Zerochan is currently blocking automated requests. Try again later.")
        parser = _GalleryParser()
        try:
            parser.feed(html)
        except Exception as exc:  # malformed markup should not crash the app
            raise SourceError(f"Could not parse Zerochan's response: {exc}") from exc
        posts: list[Post] = []
        for item in parser.items:
            post = self._to_post(item)
            if post is not None:
                posts.append(post)
        posts = posts[: query.limit]
        return SourcePage(posts=posts, has_more=bool(posts))

    def _to_post(self, item: dict) -> Post | None:
        post_id = str(item.get("id") or "")
        if not post_id.isdigit():
            return None
        thumb = _jpg_thumbnail(str(item.get("thumb") or ""))
        full = str(item.get("full") or "")
        if not thumb:
            return None
        if not full:
            full = thumb
        preview = re.sub(r"\.full\.(\d+)\.\w+$", r".600.\1.jpg", full)
        if preview == full and not re.search(r"\.(?:jpg|jpeg|png|gif|webp)$", preview, re.I):
            preview = thumb
        width, height = self._dimensions(item)
        tags = tuple(str(tag) for tag in item.get("tags", [])[:60])
        title_lines = [line.strip() for line in str(item.get("title") or "").splitlines() if line.strip()]
        artist = ""
        for line in title_lines[1:]:
            if not line.lower().startswith(("uploaded by", "download")):
                artist = line
                break
        if not artist:
            artist = str(item.get("alt") or "")
        return Post(
            id=post_id,
            source=self.id,
            source_name=self.name,
            thumb_url=thumb,
            preview_url=preview,
            file_url=full,
            page_url=str(item.get("page") or f"{BASE_URL}/{post_id}"),
            width=width,
            height=height,
            tags=tags,
            rating="safe",
            artist=artist,
            ext=extension_from_url(full, fallback="jpg"),
            referer=BASE_URL + "/",
        )

    @staticmethod
    def _dimensions(item: dict) -> tuple[int, int]:
        for line in str(item.get("title") or "").splitlines():
            match = _DIMENSIONS.search(line)
            if match:
                return int(match.group(1)), int(match.group(2))
        return int(item.get("width") or 0), int(item.get("height") or 0)
