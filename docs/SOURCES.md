# Writing a Mizuki source

A source is a small Python adapter that turns a `SearchQuery` into a
`SourcePage`. Mizuki ships five adapters, and you can add your own without
patching the app.

## Where plugins live

```text
~/.local/share/mizuki/sources/
```

Every `.py` file in that directory is loaded at startup. The loader looks for a
module-level `SOURCE` object (either an instance or a class), or — if `SOURCE`
is absent — any `Source` subclass defined in the module. Source ids must be
unique and must not collide with a built-in source.

Errors while loading a plugin are logged and skipped; one broken plugin cannot
prevent Mizuki from starting.

## The contract

```python
from mizuki.models import Post, SearchQuery, SourcePage
from mizuki.sources.base import Source


class MySource(Source):
    id = "mysource"                 # stable, lowercase, unique
    name = "My Source"              # shown in the sidebar
    description = "One line shown in Preferences."
    homepage = "https://example.org"
    icon = "image-x-generic-symbolic"
    supports_nsfw = True
    max_tags = 0                    # documented source limit, 0 = no limit
    popular_tags = ("landscape", "sky", "1girl")

    def search(self, query: SearchQuery) -> SourcePage:
        ...

    def suggest_tags(self, prefix: str, limit: int = 8) -> list[str]:
        return []

    def headers(self) -> dict[str, str]:
        return {}
```

`SOURCE = MySource` (or `SOURCE = MySource()`) at module level.

### `search(query)`

Called on a worker thread. Do not touch GTK objects, widgets, settings or the
download manager from here. Blocking network requests are expected.

Use the shared helpers in `mizuki.net`, which add the project `User-Agent`,
timeouts, retry/backoff and friendly `NetworkError` messages:

```python
from mizuki import net
from mizuki.models import Post, SourcePage

raw_posts = net.get_json(
    "https://example.org/posts.json",
    params={"tags": " ".join(query.tags), "page": query.page},
)
```

Then build `Post` objects:

```python
posts = [
    Post(
        id=str(item["id"]),
        source=self.id,
        source_name=self.name,
        thumb_url=item["thumbnail"],
        preview_url=item.get("preview", item["file"]),
        file_url=item["file"],
        page_url=f"{self.homepage}/post/{item['id']}",
        width=int(item.get("width", 0)),
        height=int(item.get("height", 0)),
        tags=tuple(item.get("tags", [])),
        rating="safe",             # safe | sensitive | questionable | explicit
        artist=item.get("artist", ""),
        ext="jpg",
        score=int(item.get("score", 0)),
        referer=self.homepage + "/",
    )
    for item in raw_posts
]
return SourcePage(posts=posts, has_more=len(raw_posts) >= query.limit)
```

### Safety requirements

Mizuki enforces safe search at two levels:

1. Your adapter should add the source's safe-rating filter when
   `query.safe` is true.
2. Mizuki also drops non-safe posts client-side when `query.safe` is true, and
   covers sensitive thumbnails when appropriate.

Set `supports_nsfw = False` if the source has no adult content. Such sources
stay filtered even when the global SFW switch is off. Never ignore
`query.safe` for an NSFW-capable source.

Strip user-provided `rating:` / `order:` metatags before adding your own, or
users could bypass the SFW filter. Built-in adapters expose a `search_tags()`
helper for this; you can copy the pattern.

### Pagination

`query.page` starts at 1. Return `has_more=True` if another page may exist.
Mizuki stops requesting when a source returns no posts or the user reaches the
240-result cap.

### Tag suggestions

`suggest_tags(prefix)` is optional and also runs on a worker thread. It is used
by the search-bar popover. Keep timeouts short and fail silently.

### Extra headers

Return per-source headers (typically `Referer` or a required API key) from
`headers()`. The base HTTP headers, including the identifying `User-Agent`, are
merged automatically.

## Example: a JSON booru

```python
from mizuki import net
from mizuki.models import Post, SourcePage
from mizuki.sources.base import Source


class ExampleBooru(Source):
    id = "example"
    name = "Example Booru"
    description = "Example adapter showing the minimum viable source."
    homepage = "https://example.org"
    icon = "image-x-generic-symbolic"
    supports_nsfw = False
    popular_tags = ("landscape",)

    def search(self, query):
        params = {"tags": " ".join(query.tags), "page": query.page}
        data = net.get_json(self.homepage + "/posts.json", params=params)
        posts = []
        for item in data:
            rating = item.get("rating", "safe")
            if query.safe and rating != "safe":
                continue
            posts.append(
                Post(
                    id=str(item["id"]),
                    source=self.id,
                    source_name=self.name,
                    thumb_url=item["thumbnail"],
                    preview_url=item["sample"],
                    file_url=item["file"],
                    page_url=item["page"],
                    width=item.get("width", 0),
                    height=item.get("height", 0),
                    tags=tuple(item.get("tags", [])),
                    rating=rating,
                    ext=item.get("ext", "jpg"),
                    referer=self.homepage + "/",
                )
            )
        return SourcePage(posts=posts, has_more=len(posts) >= query.limit)


SOURCE = ExampleBooru
```

## Testing an adapter

Sources are toolkit-independent, so you can exercise them from a terminal:

```bash
PYTHONPATH=src python3 - <<'PY'
from mizuki.sources.example import ExampleBooru
from mizuki.models import SearchQuery

page = ExampleBooru().search(SearchQuery(text="landscape", safe=True, limit=3))
for post in page.posts:
    print(post.id, post.rating, post.thumb_url)
PY
```

Check:

- Safe search really filters adult posts.
- Thumbnails and previews return image bytes (not an HTML challenge page).
- `page_url` opens the post.
- `has_more` behaves correctly and pagination does not loop forever.
- Filenames are sensible (`Post.filename()` handles sanitisation).

Add unit tests under `tests/` where parsing logic is non-trivial, following the
Zerochan parser tests as an example.

## Built-in sources

| Module | Source |
|---|---|
| `mizuki.sources.danbooru` | Danbooru |
| `mizuki.sources.safebooru` | Safebooru (safebooru.org) |
| `mizuki.sources.moebooru` | Konachan, Yande.re |
| `mizuki.sources.zerochan` | Zerochan |

## Licensing and etiquette

Only distribute adapters you are allowed to share. Respect each site's API
terms and rate limits, do not bypass authentication, paywalls or challenge
pages, and keep the honest project `User-Agent`. Mizuki does not grant you any
rights to the artwork it displays.
