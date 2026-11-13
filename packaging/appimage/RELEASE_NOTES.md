# Mizuki v0.1.0

**A fast, native anime artwork browser for the Linux desktop.**

First public release of Mizuki, built with Python 3, PyGObject, GTK 4 and libadwaita.

### Included

- Danbooru, Safebooru, Konachan, Yande.re and Zerochan
- Tag search, source-specific filters and per-source sort controls
- Justified gallery for landscape, portrait and square artwork
- Collapsible sidebar and removable recent searches
- Cached thumbnails, preview prefetching and disk-backed image cache
- Safe-search controls, favourites and background downloads

### Download

**Recommended — portable launcher (no FUSE required):**

- `Mizuki-0.1.0-x86_64.run`

Download it, make it executable, and run it:

```bash
chmod +x Mizuki-0.1.0-x86_64.run
./Mizuki-0.1.0-x86_64.run
```

The launcher extracts the bundled application to `~/.cache/mizuki/` and runs it. It does not need FUSE, root access or system GTK packages.

**AppImage (requires FUSE 2):**

- `Mizuki-0.1.0-x86_64.AppImage`

Some modern distributions ship FUSE 3 but not the legacy `libfuse2` library required by AppImage. If the AppImage reports that FUSE is missing, install your distribution's FUSE 2 package, or use:

```bash
./Mizuki-0.1.0-x86_64.AppImage --appimage-extract-and-run
```

The AppImage and portable launcher are built on Ubuntu 24.04 and require glibc 2.39 or newer.

A macOS `.app` is not included in this release.
