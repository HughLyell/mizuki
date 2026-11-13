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
from .base import META_PREFIXES, Source, SourceError

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
    tag_separator = " "
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
        self._resolution_cache: dict[str, tuple[float, list[str]]] = {}
        self._html_cache: dict[str, tuple[float, str, str]] = {}
        self._cache_lock = threading.Lock()

    def headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml",
            "Referer": BASE_URL + "/",
        }

    @staticmethod
    def _normalise_terms(text: str) -> list[str]:
        """Parse Zerochan query text.

        Zerochan tag names can contain spaces, so unless the user supplied
        commas, the whole text is treated as one tag first. Comma-separated
        groups are treated as separate tags and may themselves contain spaces.
        """
        raw = str(text or "").strip()
        if not raw:
            return []
        groups = raw.split(",") if "," in raw else [raw]
        terms: list[str] = []
        for group in groups:
            term = group.strip().strip('"').strip()
            term = " ".join(term.replace("_", " ").split())
            if not term:
                continue
            if term.lower().startswith(META_PREFIXES):
                continue
            if term not in terms:
                terms.append(term)
        return terms

    def query_tags(self, query: SearchQuery) -> list[str]:
        return self._normalise_terms(query.text)

    def display_tags(self, text: str) -> list[str]:
        return self._normalise_terms(text)

    def join_tags(self, tags: list[str]) -> str:
        return ", ".join(tags)

    def _path_for_tags(self, tags: list[str]) -> str:
        clean = [tag for tag in tags if tag and not tag.startswith("-")][:3]
        if not clean:
            return "/"
        return "/" + ",".join(self._encode_tag_path(tag).lstrip("/") for tag in clean)

    def search_path(self, query: SearchQuery) -> str:
        return self._path_for_tags(self.query_tags(query))

    @staticmethod
    def _encode_tag_path(tag: str) -> str:
        return "/" + urllib.parse.quote(
            tag.replace("_", " ").replace(" ", "+"),
            safe="+()_.-",
        )

    @staticmethod
    def _typo_candidates(tag: str) -> list[str]:
        """Generate conservative single-letter-collapse candidates."""
        candidates: list[str] = []
        seen = {tag.strip().lower()}
        for index in range(1, len(tag)):
            if tag[index].lower() != tag[index - 1].lower():
                continue
            candidate = tag[:index] + tag[index + 1:]
            key = candidate.strip().lower()
            if key and key not in seen:
                seen.add(key)
                candidates.append(candidate)
        return candidates[:5]

    def _find_correction(self, tag: str) -> tuple[str, str] | None:
        # Each candidate costs a request, so keep this tight: a tag that 404s
        # must not stall the search for tens of seconds.
        for candidate in self._typo_candidates(tag)[:2]:
            try:
                html = self._fetch_html(BASE_URL + self._encode_tag_path(candidate), timeout=8)
            except net.NetworkError:
                continue
            if "Just a moment" in html and "cloudflare" in html.lower():
                continue
            try:
                parser = _GalleryParser()
                parser.feed(self._gallery_fragment(html))
            except Exception:
                continue
            if parser.items:
                log.info("Zerochan correction: %s -> %s", tag, candidate)
                return html, candidate
        return None

    def _url_for_tags(self, tags: list[str], query: SearchQuery) -> str:
        path = self._path_for_tags(tags)
        params: list[str] = []
        if query.page > 1:
            params.append(f"p={query.page}")
        if query.sort == "newest":
            params.append("s=id")
        elif query.sort == "score":
            params.extend(["s=fav", "t=0"])
        elif query.sort == "random":
            params.append("s=random")
        if params:
            return f"{BASE_URL}{path}?{'&'.join(params)}"
        return f"{BASE_URL}{path}"

    def search_url(self, query: SearchQuery) -> str:
        return self._url_for_tags(self.query_tags(query), query)

    def _tag_variants(self, query: SearchQuery) -> list[list[str]]:
        terms = self.query_tags(query)
        raw = str(query.text or "").strip()
        if not terms:
            return [[]]
        if "," in raw:
            return [terms]
        if len(terms) == 1:
            words = [word for word in re.split(r"\s+", terms[0]) if word]
            if len(words) > 1:
                return [[terms[0]], words]
        return [terms]

    def _cache_put(self, cache: dict, key: str, value, *, ttl: float, max_entries: int) -> None:
        """Store a cache entry, pruning expired entries and capping the size.

        Must be called with ``self._cache_lock`` held.
        """
        now = time.time()
        for stale_key in [k for k, entry in cache.items() if now - entry[0] >= ttl]:
            cache.pop(stale_key, None)
        while len(cache) >= max_entries:
            cache.pop(next(iter(cache)))
        cache[key] = value

    def _fetch_page(self, url: str, timeout: int = 25) -> tuple[str, str]:
        now = time.time()
        with self._cache_lock:
            cached = self._html_cache.get(url)
            if cached and now - cached[0] < 60:
                return cached[1], cached[2]
        html, final_url = net.get_text_with_url(url, headers=self.headers(), timeout=timeout)
        with self._cache_lock:
            self._cache_put(self._html_cache, url, (now, html, final_url), ttl=60, max_entries=80)
        return html, final_url

    def _fetch_html(self, url: str, timeout: int = 25) -> str:
        return self._fetch_page(url, timeout=timeout)[0]

    @staticmethod
    def _requested_query(url: str) -> str:
        return urllib.parse.urlparse(url).query

    @staticmethod
    def _gallery_fragment(html: str) -> str:
        match = re.search(
            r'<ul[^>]+id="thumbs\d+"[^>]*>(.*?)</ul>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return html
        return "<ul>" + match.group(1) + "</ul>"

    def _parse_posts(self, html: str) -> list[Post]:
        parser = _GalleryParser()
        parser.feed(self._gallery_fragment(html))
        posts: list[Post] = []
        for item in parser.items:
            post = self._to_post(item)
            if post is not None:
                posts.append(post)
        return posts

    def _parse_posts_or_raise(self, html: str) -> list[Post]:
        try:
            return self._parse_posts(html)
        except Exception as exc:
            raise SourceError(f"Could not parse Zerochan's response: {exc}") from exc

    def search(self, query: SearchQuery) -> SourcePage:
        original_terms = self.query_tags(query)
        variants = self._tag_variants(query)
        resolution_key = " ".join(str(query.text or "").lower().split())
        now = time.time()
        with self._cache_lock:
            cached = self._resolution_cache.get(resolution_key)
            if cached and now - cached[0] < _SUPPLEMENT_TTL:
                variants = [cached[1]] + [variant for variant in variants if variant != cached[1]]

        html: str | None = None
        posts: list[Post] = []
        chosen_tags: list[str] = []
        corrected_from = ""
        corrected_tag = ""
        no_results_notice = ""
        member_only = False
        for index, tags in enumerate(variants):
            requested_url = self._url_for_tags(tags, query)
            try:
                html, final_url = self._fetch_page(requested_url)
            except net.NetworkError as exc:
                if exc.status != 404:
                    raise SourceError(str(exc)) from exc
                if index == 0 and query.page == 1 and len(tags) == 1:
                    correction = self._find_correction(tags[0])
                    if correction:
                        html, corrected_tag = correction
                        corrected_from = tags[0]
                        chosen_tags = [corrected_tag]
                        posts = self._parse_posts_or_raise(html)
                        break
                no_results_notice = (
                    f"Zerochan has no tag matching '{original_terms[0]}'."
                    if len(original_terms) == 1
                    else "Zerochan has no tag matching this search."
                )
                continue
            requested_query = self._requested_query(requested_url)
            if requested_query:
                requested_parts = urllib.parse.urlparse(requested_url)
                final_parts = urllib.parse.urlparse(final_url)
                if (
                    final_parts.path != requested_parts.path
                    or final_parts.query != requested_query
                ):
                    canonical = urllib.parse.urlunparse(
                        (
                            final_parts.scheme,
                            final_parts.netloc,
                            final_parts.path,
                            "",
                            requested_query,
                            "",
                        )
                    )
                    if canonical != final_url:
                        html, final_url = self._fetch_page(canonical)
            if "Just a moment" in html and "cloudflare" in html.lower():
                raise SourceError("Zerochan is currently blocking automated requests. Try again later.")
            posts = self._parse_posts_or_raise(html)
            member_only = "members only" in html.lower()
            if posts or member_only or not tags:
                chosen_tags = tags
                break
            no_results_notice = (
                f"Zerochan has no tag matching '{original_terms[0]}'."
                if len(original_terms) == 1
                else "Zerochan has no tag matching this search."
            )
        else:
            return SourcePage(
                posts=[],
                has_more=False,
                notice=no_results_notice or "Zerochan has no tag matching this search.",
            )

        if html is None:
            return SourcePage(posts=[], has_more=False, notice=no_results_notice)

        # The typo-correction branch breaks out of the variant loop before the
        # in-loop member_only assignment runs, so always derive it from the
        # final successful HTML; otherwise corrected searches never trigger
        # the guest-limit supplementation below.
        member_only = "members only" in html.lower()

        if chosen_tags:
            with self._cache_lock:
                self._cache_put(
                    self._resolution_cache,
                    resolution_key,
                    (now, list(chosen_tags)),
                    ttl=_SUPPLEMENT_TTL,
                    max_entries=128,
                )

        has_more = bool(posts) and query.sort != "random"
        notice = ""
        supplement_tag = corrected_tag or (chosen_tags[0] if len(chosen_tags) == 1 else "")
        if (
            member_only
            and query.page == 1
            and supplement_tag
            and 0 < len(posts) <= 12
        ):
            extra, notice = self._supplement(supplement_tag, posts)
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
        if member_only and not posts and not notice:
            notice = "Zerochan hides this tag behind a member login."
        notice_parts: list[str] = []
        if corrected_from:
            notice_parts.append(
                f"No Zerochan tag '{corrected_from}'; showing results for '{corrected_tag}'."
            )
        if notice:
            notice_parts.append(notice)
        if notice_parts:
            limit = max(query.limit, 100)
        else:
            limit = query.limit
        return SourcePage(
            posts=posts[:limit],
            has_more=has_more,
            notice=" ".join(notice_parts),
        )

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
                html = self._fetch_html(
                    f"{BASE_URL}/similar3?id={post.id}&ajax",
                    timeout=20,
                )
                parser = _GalleryParser()
                parser.feed(self._gallery_fragment(html))
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
            page_html = self._fetch_html(public_posts[0].page_url, timeout=20)
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
            self._cache_put(
                self._supplement_cache,
                key,
                (now, result, notice),
                ttl=_SUPPLEMENT_TTL,
                max_entries=64,
            )
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
        preview = re.sub(r"\.full\.(\d+)\.(\w+)$", r".600.\1.\2", full)
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
