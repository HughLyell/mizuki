"""Colour accents and CSS loading."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from ..config import resource_path  # noqa: E402

log = logging.getLogger(__name__)

# Material You-inspired accents. Values are tuned for both light and dark
# colour schemes so text keeps a comfortable contrast ratio.
ACCENTS: dict[str, dict[str, dict[str, str]]] = {
    "mauve": {
        "light": {"bg": "#6d3ed1", "fg": "#ffffff", "color": "#5b32b4"},
        "dark": {"bg": "#8b5cf6", "fg": "#ffffff", "color": "#c4b5fd"},
    },
    "blue": {
        "light": {"bg": "#1a73e8", "fg": "#ffffff", "color": "#0b57d0"},
        "dark": {"bg": "#3584e4", "fg": "#ffffff", "color": "#99c1f1"},
    },
    "green": {
        "light": {"bg": "#2e7d32", "fg": "#ffffff", "color": "#1b5e20"},
        "dark": {"bg": "#3a944a", "fg": "#ffffff", "color": "#8ff0a4"},
    },
    "peach": {
        "light": {"bg": "#bc5800", "fg": "#ffffff", "color": "#8f4300"},
        "dark": {"bg": "#e8873a", "fg": "#1c1006", "color": "#ffbe8a"},
    },
    "rose": {
        "light": {"bg": "#b3245c", "fg": "#ffffff", "color": "#8e1b48"},
        "dark": {"bg": "#e04d86", "fg": "#ffffff", "color": "#ffa8c7"},
    },
}

ACCENT_LABELS = {
    "mauve": "Mauve",
    "blue": "Ocean",
    "green": "Matcha",
    "peach": "Peach",
    "rose": "Rose",
}


class ThemeManager:
    def __init__(self) -> None:
        self._accent_provider: Gtk.CssProvider | None = None
        self._base_provider: Gtk.CssProvider | None = None

    def install(self, display: Gdk.Display, *, accent: str, dark: bool) -> None:
        self.load_base_css(display)
        self.apply_accent(display, accent, dark)
        icon_theme = Gtk.IconTheme.get_for_display(display)
        icons = resource_path("icons")
        if icons.is_dir():
            icon_theme.add_search_path(str(icons))

    def load_base_css(self, display: Gdk.Display) -> None:
        path = resource_path("style.css")
        provider = Gtk.CssProvider()
        try:
            provider.load_from_path(str(path))
        except Exception:
            log.exception("Could not load stylesheet at %s", path)
            return
        if self._base_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._base_provider)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._base_provider = provider

    def apply_accent(self, display: Gdk.Display, name: str, dark: bool) -> None:
        palette = ACCENTS.get(name) or ACCENTS["mauve"]
        values = palette["dark" if dark else "light"]
        css = (
            f"@define-color accent_bg_color {values['bg']};\n"
            f"@define-color accent_fg_color {values['fg']};\n"
            f"@define-color accent_color {values['color']};\n"
        )
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(css.encode("utf-8"))
        except Exception:
            log.exception("Could not apply accent colour")
            return
        if self._accent_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._accent_provider)
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self._accent_provider = provider
