"""Command-line entry point."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from . import APP_NAME, __version__
from . import config


def _configure_logging() -> None:
    debug = bool(os.environ.get("MIZUKI_DEBUG"))
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if debug else logging.INFO)
    stream.setFormatter(formatter)

    handlers: list[logging.Handler] = [stream]
    try:
        config.ensure_dir(config.CACHE_DIR)
        file_handler = logging.handlers.RotatingFileHandler(
            config.CACHE_DIR / "mizuki.log",
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        pass

    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)
    logging.getLogger(__name__).info("Mizuki %s starting", __version__)


def _source_check() -> int:
    """Network smoke test for every built-in source (no GTK required)."""
    from .models import SearchQuery
    from .sources import SourceRegistry

    registry = SourceRegistry(include_plugins=False)
    failures = 0
    for source in registry.all():
        try:
            page = source.search(SearchQuery(text="landscape", limit=3, safe=True))
            print(
                f"{source.id}: {len(page.posts)} posts (has_more={page.has_more})",
                flush=True,
            )
            if page.posts:
                print(f"  {page.posts[0].thumb_url}", flush=True)
        except Exception as exc:
            failures += 1
            print(f"{source.id}: ERROR {type(exc).__name__}: {exc}", flush=True)
    print(f"Sources checked: {len(registry.all())}, failures: {failures}", flush=True)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in argv[1:]
    if smoke_test:
        argv = [argument for argument in argv if argument != "--smoke-test"]
        os.environ["MIZUKI_SMOKE_TEST"] = "1"
    source_check = "--source-check" in argv[1:]
    if source_check:
        argv = [argument for argument in argv if argument != "--source-check"]
    if any(arg in ("--version", "-v") for arg in argv[1:]):
        print(f"{APP_NAME} {__version__}")
        return 0

    _configure_logging()
    if source_check:
        return _source_check()

    try:
        import gi
    except ImportError:
        print(
            "Mizuki needs PyGObject. Install it with your distribution's package "
            "manager (for example: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1).",
            file=sys.stderr,
        )
        return 1

    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        gi.require_version("Gdk", "4.0")
    except ValueError as exc:
        print(f"Mizuki needs GTK 4 and libadwaita: {exc}", file=sys.stderr)
        return 1

    from .application import MizukiApplication

    app = MizukiApplication()
    return app.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
