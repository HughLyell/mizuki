"""Open external URIs (browser, file manager) robustly.

The naive ``Gio.AppInfo.launch_default_for_uri`` breaks in two common cases:

- Sandboxes (Flatpak): the child must go through the xdg-desktop-portal.
- The AppImage: the bundled GTK environment (``LD_LIBRARY_PATH``,
  ``GTK_*``/``GIO_*`` module paths, ``XDG_DATA_DIRS`` with the AppDir first)
  leaks into the launched browser/file manager and breaks or confuses it.

This helper picks a strategy that avoids both.
"""

from __future__ import annotations

import logging
import os
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .launch_env import clean_appimage_env  # noqa: E402

log = logging.getLogger(__name__)


def _report(on_error, uri: str, exc: Exception) -> None:
    message = getattr(exc, "message", None) or str(exc)
    log.warning("Could not open %s: %s", uri, message)
    if on_error is not None:
        on_error(f"Could not open link: {message}")


def _launch_via_xdg_open(uri: str, on_error) -> bool:
    try:
        subprocess.Popen(
            ["xdg-open", uri],
            env=clean_appimage_env(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except FileNotFoundError:
        log.info("xdg-open is unavailable; falling back to Gtk.UriLauncher")
        return False
    except (OSError, GLib.Error) as exc:
        _report(on_error, uri, exc)
        return True


def _launch_via_gtk(parent, uri: str, on_error) -> None:
    window = None
    if parent is not None:
        root = parent.get_root()
        if isinstance(root, Gtk.Window):
            window = root

    if hasattr(Gtk, "UriLauncher"):
        try:
            launcher = Gtk.UriLauncher.new(uri)
            launcher.launch(window, None, _on_uri_launched, (uri, on_error))
            return
        except Exception:
            log.exception("Gtk.UriLauncher failed for %s", uri)
    # GTK < 4.10 fallback.
    try:
        ok = Gio.AppInfo.launch_default_for_uri(uri, None)
        if not ok:
            _report(on_error, uri, GLib.Error("No application handles this link"))
    except GLib.Error as exc:
        _report(on_error, uri, exc)


def _on_uri_launched(launcher, result, data) -> None:
    uri, on_error = data
    try:
        launcher.launch_finish(result)
    except GLib.Error as exc:
        _report(on_error, uri, exc)


def launch_external(uri: str, *, parent=None, on_error=None) -> None:
    """Open ``uri`` in the default browser / file manager.

    ``parent`` is the widget the request originates from (used to pick a
    transient window for the portal). ``on_error`` is called with a friendly
    message if the launch fails.
    """
    if not uri:
        if on_error is not None:
            on_error("No link available")
        return
    if os.environ.get("APPDIR") and _launch_via_xdg_open(uri, on_error):
        return
    _launch_via_gtk(parent, uri, on_error)
