"""Command-line entry point."""

from __future__ import annotations

import logging
import os
import sys

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in argv[1:]
    if smoke_test:
        argv = [argument for argument in argv if argument != "--smoke-test"]
        os.environ["MIZUKI_SMOKE_TEST"] = "1"
    if any(arg in ("--version", "-v") for arg in argv[1:]):
        print(f"{APP_NAME} {__version__}")
        return 0

    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("MIZUKI_DEBUG") else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

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
