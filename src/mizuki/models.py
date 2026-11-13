"""Small, toolkit-independent data model shared by sources and the UI."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

SAFE = "safe"
SENSITIVE = "sensitive"
QUESTIONABLE = "questionable"
EXPLICIT = "explicit"

RATING_LABELS = {
    SAFE: "Safe",
    SENSITIVE: "Sensitive",
    QUESTIONABLE: "Questionable",
    EXPLICIT: "Explicit",
}

IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp"})

_RATING_ALIASES = {
    "g": SAFE,
    "general": SAFE,
    "safe": SAFE,
    "s": SENSITIVE,
    "sensitive": SENSITIVE,
    "q": QUESTIONABLE,
    "questionable": QUESTIONABLE,
    "e": EXPLICIT,
    "explicit": EXPLICIT,
}

_UNSAFE_CHARS = re.compile(r"[^\w.\-()\[\] ]+", re.UNICODE)
_SPACE_RUN = re.compile(r"\s+")
_EXT_FROM_URL = re.compile(r"\.([a-zA-Z0-9]{2,5})(?:[?#].*)?$")


def normalize_rating(raw: object) -> str:
    """Normalise a source rating (``g``, ``s``, ``q``, ``e`` …) to our model."""
    value = str(raw or "").strip().lower()
    return _RATING_ALIASES.get(value, SAFE)


def parse_tags(text: str) -> list[str]:
    """Split a search string into tags, honouring quoted phrases.

    Booru syntax is whitespace-separated. Quotes let users search tags that
    contain a space (rare, but some sites accept them), and ``-tag`` exclusion
    syntax is preserved untouched.
    """
    tags: list[str] = []
    current: list[str] = []
    quoted = False
    for char in text.strip():
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                tags.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tags.append("".join(current))
    return tags


def split_comma_groups(text: str) -> list[str] | None:
    """Split explicitly comma-separated tag groups, preserving spaces.

    Returns ``None`` when the text has no comma, meaning the caller should use
    normal whitespace-separated parsing.
    """
    raw = str(text or "").strip()
    if "," not in raw:
        return None
    groups: list[str] = []
    for group in raw.split(","):
        term = " ".join(group.strip().strip('"').split())
        if term and term not in groups:
            groups.append(term)
    return groups


def sanitize_component(value: str, fallback: str = "artwork", max_length: int = 64) -> str:
    """Make a string safe for use as part of a filename."""
    normalised = unicodedata.normalize("NFKC", str(value or ""))
    normalised = _UNSAFE_CHARS.sub("_", normalised)
    normalised = _SPACE_RUN.sub("_", normalised).strip("._- ")
    if not normalised:
        normalised = fallback
    return normalised[:max_length].rstrip("._- ") or fallback


def extension_from_url(url: str, fallback: str = "jpg") -> str:
    match = _EXT_FROM_URL.search(url or "")
    if not match:
        return fallback
    ext = match.group(1).lower()
    if ext == "jpeg":
        return "jpg"
    return ext if ext in IMAGE_EXTENSIONS else fallback


def parse_filter_tags(text: str) -> list[str]:
    """Parse a comma-separated per-source result filter."""
    tags: list[str] = []
    for raw in str(text or "").split(","):
        tag = raw.strip().lower().replace("_", " ")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def post_matches_filter(post: Post, text: str) -> bool:
    """Return True if ``post`` contains every comma-separated filter tag."""
    wanted = parse_filter_tags(text)
    if not wanted:
        return True
    available = {tag.lower().replace("_", " ") for tag in post.tags}
    return all(tag in available for tag in wanted)


@dataclass(frozen=True, slots=True)
class Post:
    """A single image post returned by a source."""

    id: str
    source: str
    source_name: str
    thumb_url: str
    preview_url: str
    file_url: str
    page_url: str
    width: int = 0
    height: int = 0
    tags: tuple[str, ...] = ()
    rating: str = SAFE
    artist: str = ""
    ext: str = "jpg"
    score: int = 0
    referer: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def is_safe(self) -> bool:
        return self.rating == SAFE

    @property
    def rating_label(self) -> str:
        return RATING_LABELS.get(self.rating, "Safe")

    @property
    def aspect_ratio(self) -> float:
        if self.width > 0 and self.height > 0:
            return self.width / self.height
        return 0.75

    @property
    def dimensions_label(self) -> str:
        if self.width and self.height:
            return f"{self.width} × {self.height}"
        return "Unknown size"

    def filename(self) -> str:
        stem = f"{sanitize_component(self.source, 'source', 24)}_{sanitize_component(self.id, 'image', 32)}"
        if self.artist:
            stem += "_" + sanitize_component(self.artist, "artist", 48)
        ext = self.ext.lower()
        if ext == "jpeg":
            ext = "jpg"
        if ext not in IMAGE_EXTENSIONS:
            ext = "jpg"
        return f"{stem}.{ext}"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source": self.source,
            "source_name": self.source_name,
            "thumb_url": self.thumb_url,
            "preview_url": self.preview_url,
            "file_url": self.file_url,
            "page_url": self.page_url,
            "width": self.width,
            "height": self.height,
            "tags": list(self.tags),
            "rating": self.rating,
            "artist": self.artist,
            "ext": self.ext,
            "score": self.score,
            "referer": self.referer,
        }

    @staticmethod
    def _coerce_int(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Post:
        raw_tags = data.get("tags") or []
        if not isinstance(raw_tags, (list, tuple)):
            raw_tags = []
        tags = tuple(str(tag) for tag in raw_tags)
        return cls(
            id=str(data.get("id", "")),
            source=str(data.get("source", "")),
            source_name=str(data.get("source_name", data.get("source", ""))),
            thumb_url=str(data.get("thumb_url", "")),
            preview_url=str(data.get("preview_url", "")),
            file_url=str(data.get("file_url", "")),
            page_url=str(data.get("page_url", "")),
            width=cls._coerce_int(data.get("width")),
            height=cls._coerce_int(data.get("height")),
            tags=tags,
            rating=normalize_rating(data.get("rating")),
            artist=str(data.get("artist") or ""),
            ext=str(data.get("ext") or "jpg"),
            score=cls._coerce_int(data.get("score")),
            referer=str(data.get("referer") or ""),
        )


@dataclass(slots=True)
class SearchQuery:
    """Everything needed to run one page of a search."""

    text: str = ""
    page: int = 1
    limit: int = 40
    safe: bool = True
    sort: str = "newest"  # newest | score | relevance | random

    @property
    def tags(self) -> list[str]:
        return [
            tag
            for tag in parse_tags(self.text)
            if not tag.lstrip("-").lower().startswith(("rating:", "order:", "status:"))
        ]

    def with_page(self, page: int) -> SearchQuery:
        return replace(self, page=page)

    def without_tag(self, tag: str) -> SearchQuery:
        remaining = [t for t in parse_tags(self.text) if t != tag]
        return replace(self, text=" ".join(remaining), page=1)


@dataclass(slots=True)
class SourcePage:
    """One page of results from one source."""

    posts: list[Post] = field(default_factory=list)
    has_more: bool = False
    notice: str = ""


@dataclass(slots=True)
class Favorite:
    post: Post
    added: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
