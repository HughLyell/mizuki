"""Zerochan adapter.

Zerochan has no dependable public JSON API (the ``?json`` endpoint is
intermittent), but its gallery pages are richly structured: every tile carries
the post id, thumbnail, dimensions, artist and a direct full-size image link.
We parse those tiles with the standard library's HTML parser and degrade
gracefully if the markup changes.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from .. import net
from ..models import Post, SearchQuery, SourcePage, extension_from_url
from .base import Source, SourceError

log = logging.getLogger(__name__)

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
_EXT_IN_TITLE = re.compile(r"\b(jpe?g|png|webp|gif)\b", re.IGNORECASE)
_SUPPLEMENT_TTL = 300


def _normalise_tag(tag: str) -> str:
    return str(tag or "").strip().lower().replace("_", " ")


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
                    "primary": "",
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
                if not self._current["primary"]:
                    self._current["primary"] = text
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

    def __init__(self) -> None:
        self._supplement_cache: dict[str, tuple[float, list[Post], str]] = {}
        self._cache_lock = threading.Lock()

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "Referer": BASE_URL + "/",
        }

    def search_path(self, query: SearchQuery) -> str:
        tags = [tag for tag in query.tags if not tag.startswith("-")][:3]
        if not tags:
            return "/"
        encoded = ",".join(
            urllib.parse.quote(tag.replace("_", " ").replace(" ", "+"), safe="+()_.-")
            for tag in tags
        )
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

        has_more = bool(posts)
        notice = ""
        member_only = "members only" in html.lower()
        tags = [tag for tag in query.tags if not tag.startswith("-")]
        if member_only and query.page == 1 and len(tags) == 1 and 0 < len(posts) <= 12:
            extra, notice = self._supplement(tags[0], posts)
            has_more = False
            if extra:
                merged = {post.id: post for post in posts}
                for post in extra:
                    merged.setdefault(post.id, post)
                posts = sorted(
                    merged.values(),
                    key=lambda post: int(post.id) if post.id.isdigit() else 0,
                    reverse=True,
                )
        if notice:
            limit = max(query.limit, 100)
        else:
            limit = query.limit
        if member_only and not posts and not notice:
            notice = "Zerochan hides this tag behind a member login."
        return SourcePage(posts=posts[:limit], has_more=has_more, notice=notice)

    def _supplement(self, tag: str, public_posts: list[Post]) -> tuple[list[Post], str]:
        """Collect related/recent images that Zerochan shows outside the grid.

        Logged-out Zerochan pages expose only a handful of images for some
        tags and hide the rest behind a member login. The site still exposes
        related images (`/similar3`) and a short recent-thumbnail list in its
        embedded tag metadata, so we merge those publicly reachable entries.
        """
        key = _normalise_tag(tag)
        now = time.time()
        with self._cache_lock:
            cached = self._supplement_cache.get(key)
            if cached and now - cached[0] < _SUPPLEMENT_TTL:
                return list(cached[1]), cached[2]

        public_ids = {post.id for post in public_posts}
        extra: dict[str, Post] = {}
        total: int | None = None

        def fetch_similar(post: Post) -> list[dict]:
            try:
                html = net.get_text(
                    f"{BASE_URL}/similar3?id={post.id}&ajax",
                    headers=self.headers(),
                    timeout=20,
                )
                parser = _GalleryParser()
                parser.feed(html)
                return parser.items
            except Exception:
                log.debug("Zerochan similar fetch failed for %s", post.id, exc_info=True)
                return []

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                similar_pages = list(pool.map(fetch_similar, public_posts[:4]))
            for items in similar_pages:
                for item in items:
                    item_tags = {_normalise_tag(value) for value in item.get("tags", [])}
                    primary = _normalise_tag(item.get("primary", ""))
                    if key not in item_tags and key != primary:
                        continue
                    post = self._to_post(item)
                    if post is not None and post.id not in public_ids:
                        extra[post.id] = post
        except Exception:
            log.debug("Zerochan similar-image supplement failed", exc_info=True)

        try:
            page_html = net.get_text(public_posts[0].page_url, headers=self.headers(), timeout=20)
            tag_data = self._extract_tag_data(page_html)
            block = None
            for name, data in tag_data.items():
                if _normalise_tag(name) == key and isinstance(data, dict):
                    block = data
                    break
            if block is None:
                for data in tag_data.values():
                    if (
                        isinstance(data, dict)
                        and public_ids & {str(value) for value in data.get("thumbnails", [])}
                    ):
                        block = data
                        break
            if block:
                raw_total = block.get("total")
                if isinstance(raw_total, (int, str)) and str(raw_total).isdigit():
                    total = int(raw_total)
                thumbnails = [
                    str(value)
                    for value in block.get("thumbnails", [])
                    if str(value).isdigit()
                ]
                missing = [
                    post_id for post_id in thumbnails
                    if post_id not in public_ids and post_id not in extra
                ][:12]

                def fetch_post(post_id: str) -> Post | None:
                    try:
                        data = net.get_json(
                            f"{BASE_URL}/{post_id}?json",
                            headers={**self.headers(), "Accept": "application/json"},
                            timeout=15,
                        )
                        return self._from_api(data) if isinstance(data, dict) else None
                    except Exception:
                        log.debug("Zerochan post fetch failed for %s", post_id, exc_info=True)
                        return None

                if missing:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        for post in pool.map(fetch_post, missing):
                            if post is not None:
                                extra[post.id] = post
        except Exception:
            log.debug("Zerochan tag-metadata supplement failed", exc_info=True)

        shown = len(public_ids | set(extra))
        if total:
            notice = (
                f"Zerochan only exposes {len(public_ids)} of {total} images for '{tag}' "
                f"to guests. Showing {shown} accessible images; sign in on Zerochan for the rest."
            )
        elif extra:
            notice = (
                f"Zerochan hides some results behind a member login; showing {shown} "
                "accessible images, including related entries."
            )
        else:
            notice = "Zerochan hides some results behind a member login."

        result = sorted(
            extra.values(),
            key=lambda post: int(post.id) if post.id.isdigit() else 0,
            reverse=True,
        )
        with self._cache_lock:
            self._supplement_cache[key] = (now, result, notice)
        return result, notice

    @staticmethod
    def _extract_tag_data(html: str) -> dict:
        marker = "tagData = "
        index = html.find(marker)
        if index < 0:
            return {}
        blob = html[index + len(marker):].lstrip()
        # Zerochan's embedded JS object has trailing commas before closing
        # braces/brackets; normalise them before parsing as JSON.
        blob = re.sub(r",(\s*[}\]])", r"\1", blob)
        try:
            data, _end = json.JSONDecoder().raw_decode(blob)
        except (ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _from_api(self, data: dict) -> Post | None:
        post_id = str(data.get("id") or "")
        full = str(data.get("full") or "")
        if not post_id.isdigit() or not full:
            return None
        preview = str(data.get("large") or data.get("medium") or full)
        thumb = _jpg_thumbnail(str(data.get("medium") or data.get("small") or preview))
        tags = tuple(str(tag) for tag in (data.get("tags") or [])[:80])
        try:
            width = int(data.get("width") or 0)
            height = int(data.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        return Post(
            id=post_id,
            source=self.id,
            source_name=self.name,
            thumb_url=thumb,
            preview_url=preview,
            file_url=full,
            page_url=f"{BASE_URL}/{post_id}",
            width=width,
            height=height,
            tags=tags,
            rating="safe",
            ext=extension_from_url(full, fallback="jpg"),
            referer=BASE_URL + "/",
        )

    def _to_post(self, item: dict) -> Post | None:
        post_id = str(item.get("id") or "")
        if not post_id.isdigit():
            return None
        thumb = _jpg_thumbnail(str(item.get("thumb") or ""))
        raw_thumb = str(item.get("thumb") or "")
        full = str(item.get("full") or "")
        if not thumb:
            return None
        if not full or full == raw_thumb or full == thumb:
            full = self._construct_full(item) or full or thumb
        preview = re.sub(r"\.full\.(\d+)\.\w+$", r".600.\1.jpg", full)
        if preview == full and not re.search(r"\.(?:jpg|jpeg|png|gif|webp)$", preview, re.I):
            preview = thumb
        width, height = self._dimensions(item)
        tags = tuple(str(tag) for tag in item.get("tags", [])[:60])
        title_lines = [line.strip() for line in str(item.get("title") or "").splitlines() if line.strip()]
        artist = ""
        for line in title_lines[1:]:
            if line.lower().startswith(("uploaded by", "download")):
                continue
            if line.isdigit():
                continue
            artist = line
            break
        if not artist:
            artist = ""
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

    def _construct_full(self, item: dict) -> str:
        """Build a full-size URL for related-image tiles that omit one."""
        post_id = str(item.get("id") or "")
        primary = str(item.get("primary") or "").strip()
        if not primary:
            primary = str(item.get("alt") or "").strip()
        if not primary and item.get("tags"):
            primary = str(item["tags"][0]).replace("_", " ")
        if not post_id.isdigit() or not primary:
            return ""
        match = _EXT_IN_TITLE.search(str(item.get("title") or ""))
        ext = (match.group(1).lower() if match else "jpg")
        if ext == "jpeg":
            ext = "jpg"
        name = primary.replace(" ", ".")
        encoded = urllib.parse.quote(name, safe=".-_")
        return f"{STATIC_URL}/{encoded}.full.{post_id}.{ext}"

    @staticmethod
    def _extension_from_title(title: str) -> str:
        match = _EXT_IN_TITLE.search(str(title or ""))
        if not match:
            return "jpg"
        ext = match.group(1).lower()
        return "jpg" if ext == "jpeg" else ext

    @staticmethod
    def _dimensions(item: dict) -> tuple[int, int]:
        for line in str(item.get("title") or "").splitlines():
            match = _DIMENSIONS.search(line)
            if match:
                return int(match.group(1)), int(match.group(2))
        return int(item.get("width") or 0), int(item.get("height") or 0)
