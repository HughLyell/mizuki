# Mizuki

**A fast, native anime artwork browser for the Linux desktop.**

Mizuki is a GTK4 + libadwaita application for browsing and downloading anime
artwork from multiple boorus and image boards. It has a Material You-inspired
interface, modular sources, tag search, SFW controls, favourites and a
background download queue — with no Electron, Node.js, WebKit or embedded
browser.

- **Stack:** Python 3 + PyGObject + GTK 4 + libadwaita
- **Target:** GNOME and KDE Plasma, Wayland or X11
- **Design:** rounded artwork cards, mauve accent palette, persistent side
  panel with sources and popular tags

## Features

- **Modular sources** — Danbooru, Safebooru, Konachan, Yande.re and Zerochan
  out of the box; add your own with a single Python file.
- **Tag search** — combine tags, use `-tag` exclusions, and get tag
  autocomplete for Danbooru-based sources.
- **Per-source filters** — each individual source can remember its own
  comma-separated tag filter to narrow loaded results. The filter is hidden for
  “All sources”.
- **Source filtering** — browse one source or merge results from every enabled
  source. Sources can be toggled in Preferences. Selecting a source in the
  sidebar immediately searches that source.
- **Resilient searches** — if one source fails or returns nothing, results from
  the other sources are still shown. Zerochan also corrects common doubled-letter
  tag typos (for example `Shylilly` → `Shylily`) when the original tag 404s.
- **SFW controls** — safe search is on by default, filters at the source, and
  requires confirmation before it can be disabled. Sources without a safe
  subset stay filtered. Sensitive thumbnails are covered by default.
- **Infinity grid** — responsive, keyboard-accessible artwork tiles with
  hover actions and a compact/comfortable/large size setting.
- **Detail view** — high-resolution preview, metadata, clickable tags,
  copy-link and open-source actions.
- **Downloads** — background queue with progress, cancel/retry, file manager
  integration, and collision-safe filenames.
- **Favourites** — stored locally as JSON.
- **Theming** — Material You accents in light and dark mode, follows the
  system colour scheme or can be forced.
- **No web wrapper** — native GTK rendering throughout, keeping startup and
  memory overhead low.

## Sources

| Source | Backend | Default rating filter | Notes |
|---|---|---|---|
| **Danbooru** | JSON API | `rating:g` when SFW is on | Anonymous API allows up to 2 tags per query |
| **Safebooru** | `safebooru.org` JSON API | Always safe | Uses the Gelbooru-style API because `safebooru.donmai.us` blocks API clients |
| **Konachan** | Moebooru JSON API | `rating:safe` when SFW is on | Wallpapers and high-resolution art |
| **Yande.re** | Moebooru JSON API | `rating:safe` when SFW is on | High-quality scans and illustrations |
| **Zerochan** | Gallery page parsing | Always safe | Guest pages hide some images behind a member login; Mizuki merges publicly reachable related/recent images and shows a notice. It does not bypass logins |

Mizuki sends an honest `User-Agent` identifying the project. Be considerate of
each site's rate limits and terms of service.

## Requirements

- Python 3.10 or newer
- GTK 4.12 or newer
- libadwaita 1.5 or newer
- PyGObject

Install the system packages for your distribution:

```bash
# Arch Linux
sudo pacman -S python-gobject gtk4 libadwaita

# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1

# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
```

PyGObject cannot be installed usefully with pip alone — it needs the system
GTK/libadwaita development libraries.

## Run from source

```bash
git clone https://github.com/HughLyell/mizuki.git
cd mizuki
chmod +x run.sh
./run.sh
```

`run.sh` puts `src/` on `PYTHONPATH` and points Mizuki at the repository's
`data/` directory.

For startup diagnostics:

```bash
MIZUKI_DEBUG=1 ./run.sh
```

## Install

```bash
meson setup build --prefix=/usr/local
meson compile -C build
sudo meson install -C build
```

Or simply:

```bash
sudo make install
```

This installs the Python package under `share/mizuki`, the stylesheet and
icons, a launcher, and the desktop/metainfo files.

### Flatpak

```bash
flatpak-builder --user --install --force-clean \
  build-aux/build \
  build-aux/io.github.hughlyell.Mizuki.yml

flatpak run io.github.hughlyell.Mizuki
```

The manifest targets `org.gnome.Platform` 48, which ships Python 3 and
PyGObject. Bump `runtime-version` when a newer runtime is available.

## Performance

Mizuki is deliberately small and native:

- No browser engine, JavaScript runtime or Node.js process.
- Source modules are imported lazily when the registry is created.
- Network search, tag autocomplete and downloads run on worker threads, so the
  GTK main loop never waits on the network.
- Thumbnails use an LRU texture cache capped at 240 textures, plus a 400 MB
  on-disk cache under `~/.cache/mizuki/thumbnails`.
- Only one full-size preview is decoded at a time; the detail dialog releases
  it when closed.
- **Target:** window visible quickly and memory below 300 MB. Measured on
  Fedora 43 with GTK 4.24 / Python 3.15: about **0.6–0.8 s cold start** and
  **~157 MB RSS** with 120 thumbnails loaded. PyGObject + GTK import alone is
  roughly 0.38 s, so the 200 ms startup goal would require a compiled
  C/Vala/Rust front end. `MIZUKI_DEBUG=1` prints UI and total startup timings.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+F` | Focus search and switch to Discover |
| `Ctrl+R` | Refresh the current page |
| `Ctrl+S` | Toggle safe search |
| `Ctrl+D` | Open Downloads |
| `Ctrl+K` | Open Favourites |
| `Ctrl+,` | Open Preferences |
| `F1` | About Mizuki |
| `Ctrl+Q` | Quit |

## Search tips

- Separate tags with spaces: `1girl landscape sky` (AND).
- Prefix a tag with `-` to exclude it on boorus that support it: `landscape -monochrome`.
- Quote a phrase to keep it together: `"blue sky"`.
- Multi-word tag names can be typed naturally: `Zenless Zone Zero`. Zerochan
  treats the phrase as one tag; booru sources automatically retry it as
  `zenless_zone_zero` when the normal tag split returns nothing.
- Use commas to search multiple tags on a source that supports spaces in tag
  names (for example Zerochan): `Zenless Zone Zero, landscape`.
- Use the **Filter** field under the source selector to narrow the currently
  loaded results for that one source. Separate tags with commas: `1girl, smile`.
  Every tag must match. The filter is hidden when “All sources” is selected and
  each source remembers its own filter.
- Click any tag chip in the detail view to search for it.
- Popular tags in the sidebar are one-click searches for the selected source.
- `rating:` and `order:` metatags typed by the user are stripped so the app's
  SFW policy and sort controls stay authoritative.
- Zerochan supports multiple tags comma-separated; Mizuki handles this
  automatically.

## Safety

Safe search is enabled on first launch:

- Danbooru, Konachan and Yande.re queries are filtered to safe ratings.
- Safebooru and Zerochan are always safe.
- Turning safe search off requires an explicit confirmation.
- Sensitive thumbnails are replaced with a “Sensitive” cover by default; this
  can be changed in Preferences → Content.

No filter is perfect. Use the app responsibly and follow each source's terms.

## Where things live

| Item | Location |
|---|---|
| Settings | `~/.config/mizuki/settings.json` |
| Favourites | `~/.config/mizuki/favorites.json` |
| Thumbnail cache | `~/.cache/mizuki/thumbnails` |
| Default downloads | `~/Pictures/Mizuki` |
| Custom sources | `~/.local/share/mizuki/sources/*.py` |

## Architecture

```text
src/mizuki/
├── application.py          # Adw.Application, actions, lifecycle
├── main.py                 # CLI entry point
├── config.py               # XDG paths, user agent, resource lookup
├── models.py               # Post, SearchQuery, tag parsing, filenames
├── settings.py             # Observable JSON settings
├── favorites.py            # Persistent favourites store
├── net.py                  # urllib helpers, retries, streaming downloads
├── sources/
│   ├── base.py             # Source interface and result merging
│   ├── danbooru.py         # Danbooru JSON adapter
│   ├── safebooru.py        # safebooru.org (Gelbooru API) adapter
│   ├── moebooru.py         # Konachan + Yande.re
│   └── zerochan.py         # Zerochan gallery parser
├── services/
│   ├── search.py           # Threaded multi-source search controller
│   ├── thumbnail_loader.py # LRU texture + disk cache
│   └── downloads.py        # Download queue and progress events
└── ui/
    ├── window.py           # Main window, sidebar, navigation
    ├── discover.py         # Search panel, filters, suggestions
    ├── grid.py             # Responsive FlowBox grid
    ├── card.py             # Artwork tile
    ├── detail.py           # Full artwork dialog
    ├── pages.py            # Favourites and Downloads pages
    ├── preferences.py      # Preferences dialog
    └── theme.py            # Accent palettes + CSS loader
```

UI code is the only layer that imports GTK. Sources and services are kept
toolkit-independent where possible, which makes them easy to test headlessly.

## Custom sources

Drop a `.py` file into `~/.local/share/mizuki/sources/` and expose a `SOURCE`
instance or class:

```python
from mizuki import net
from mizuki.models import Post, SourcePage
from mizuki.sources.base import Source


class MyBooru(Source):
    id = "mybooru"
    name = "My Booru"
    description = "A private collection"
    homepage = "https://example.org"
    icon = "image-x-generic-symbolic"
    supports_nsfw = False
    popular_tags = ("landscape", "sky")

    def search(self, query):
        data = net.get_json(
            "https://example.org/posts.json",
            params={"tags": " ".join(query.tags), "page": query.page},
        )
        posts = [
            Post(
                id=str(item["id"]),
                source=self.id,
                source_name=self.name,
                thumb_url=item["thumb"],
                preview_url=item["preview"],
                file_url=item["file"],
                page_url=item["page"],
                rating="safe",
                ext="jpg",
            )
            for item in data
        ]
        return SourcePage(posts=posts, has_more=len(posts) > 0)


SOURCE = MyBooru
```

See [docs/SOURCES.md](docs/SOURCES.md) for the full adapter contract, safety
requirements and testing advice.

## Development

```bash
make test     # pure-Python unit tests (no display needed)
make lint     # byte-compile every module
make run      # launch from the checkout
```

The test suite covers tag parsing, query construction, rating normalisation,
filename safety, the Zerochan markup parser, result merging, settings
validation and the favourites store.

### GUI smoke test

Unit tests cannot catch GTK API mistakes, so a display-backed smoke test builds
every major widget offline and exits:

```bash
./run.sh --smoke-test
# or
bash scripts/gui-check.sh
```

`gui-check.sh` captures the output in
`~/.cache/mizuki/gui-check/smoke.log` and exits non-zero on a crash. Add
`--screenshot` to launch the real app for a few seconds and capture the screen
with `grim`, `gnome-screenshot`, Spectacle or ImageMagick:

```bash
bash scripts/gui-check.sh --screenshot
```

To check the network adapters without GTK:

```bash
./run.sh --source-check
```

For a scripted visual walkthrough (main grid, detail dialog, preferences,
favourites, downloads, source switch, tag search, and a real download), run:

```bash
./run.sh --demo
```

Screenshots are written to `~/.cache/mizuki/gui-check/demo/`.

Application logs are always written to `~/.cache/mizuki/mizuki.log`.

## Legal

Mizuki is an unofficial client. It is not affiliated with Danbooru,
Safebooru, Konachan, Yande.re or Zerochan. Artwork and metadata belong to their
respective artists and sites. Respect copyright, site terms and rate limits.
Prefer opening the source page to support artists.

## License

MIT © 2026 Hugh Lyell. See [LICENSE](LICENSE).
