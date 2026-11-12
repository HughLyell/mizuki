"""Responsive artwork grid with progressive loading."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..models import Post  # noqa: E402
from .card import ImageCard  # noqa: E402

GRID_SIZES: dict[str, tuple[int, int, int]] = {
    "compact": (146, 204, 10),
    "comfortable": (182, 254, 8),
    "large": (226, 316, 6),
}

MAX_POSTS = 240


class PostGrid(Gtk.ScrolledWindow):
    def __init__(
        self,
        loader,
        settings,
        favorites,
        *,
        on_activate,
        on_download,
        on_favorite,
        on_load_more=None,
        show_footer: bool = True,
        max_width: int = 1440,
    ) -> None:
        super().__init__()
        self.loader = loader
        self.settings = settings
        self.favorites = favorites
        self.on_activate = on_activate
        self.on_download = on_download
        self.on_favorite = on_favorite
        self.on_load_more = on_load_more
        self.show_footer = show_footer
        self._cards: dict[str, ImageCard] = {}
        self._posts: list[Post] = []
        self._loading = False
        self._has_more = False
        self._size = settings.get("grid_size", "comfortable")
        if self._size not in GRID_SIZES:
            self._size = "comfortable"

        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.add_css_class("content-area")

        clamp = Adw.Clamp()
        clamp.set_maximum_size(max_width)
        clamp.set_tightening_threshold(1100)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(18)
        outer.set_margin_bottom(28)
        outer.set_margin_start(24)
        outer.set_margin_end(24)

        self.flow = Gtk.FlowBox()
        self.flow.add_css_class("image-grid")
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(True)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_halign(Gtk.Align.FILL)
        self.flow.set_row_spacing(16)
        self.flow.set_column_spacing(16)
        outer.append(self.flow)

        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.footer.set_halign(Gtk.Align.CENTER)
        self.footer.set_margin_top(8)
        self.footer.set_margin_bottom(12)
        self.footer_spinner = Gtk.Spinner()
        self.footer_spinner.set_size_request(24, 24)
        self.footer.append(self.footer_spinner)
        self.footer_button = Gtk.Button(label="Load more")
        self.footer_button.add_css_class("pill-button")
        self.footer_button.connect("clicked", self._request_more)
        self.footer.append(self.footer_button)
        self.footer_label = Gtk.Label(label="")
        self.footer_label.add_css_class("footer-label")
        self.footer.append(self.footer_label)
        if show_footer:
            outer.append(self.footer)
        clamp.set_child(outer)
        self.set_child(clamp)

        self._apply_grid_size()
        adjustment = self.get_vadjustment()
        adjustment.connect("value-changed", self._on_scrolled)
        self._sync_footer()

    # -- public API ------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self._posts)

    @property
    def posts(self) -> list[Post]:
        return list(self._posts)

    def clear(self) -> None:
        child = self.flow.get_first_child()
        while child is not None:
            self.flow.remove(child)
            child = self.flow.get_first_child()
        self._cards.clear()
        self._posts.clear()
        self._loading = False
        self._has_more = False
        self._sync_footer()
        self.scroll_to_top()

    def append(self, posts: list[Post]) -> list[ImageCard]:
        added: list[ImageCard] = []
        favorite_keys = {post.key for post in self.favorites.all()}
        blurred = bool(self.settings.get("blur_nsfw", True))
        width, height, _ = GRID_SIZES[self._size]
        for post in posts:
            if post.key in self._cards or len(self._posts) >= MAX_POSTS:
                continue
            card = ImageCard(
                post,
                self.loader,
                size=(width, height),
                favorite=post.key in favorite_keys,
                blur_nsfw=blurred,
                on_activate=self.on_activate,
                on_download=self.on_download,
                on_favorite=self.on_favorite,
            )
            self.flow.append(card)
            self._cards[post.key] = card
            self._posts.append(post)
            added.append(card)
        self._sync_footer()
        return added

    def set_state(self, *, loading: bool | None = None, has_more: bool | None = None) -> None:
        if loading is not None:
            self._loading = loading
        if has_more is not None:
            self._has_more = has_more
        self._sync_footer()

    def set_grid_size(self, size: str) -> None:
        if size not in GRID_SIZES:
            return
        self._size = size
        self._apply_grid_size()
        width, height, _ = GRID_SIZES[size]
        for card in self._cards.values():
            card.set_tile_size(width, height)

    def refresh_favorites(self) -> None:
        favorite_keys = {post.key for post in self.favorites.all()}
        for key, card in self._cards.items():
            card.set_favorite(key in favorite_keys)

    def set_blurred(self, blurred: bool) -> None:
        for card in self._cards.values():
            card.set_blurred(blurred)

    def scroll_to_top(self) -> None:
        GLib.idle_add(self._scroll_top)

    def _scroll_top(self):
        adjustment = self.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(0)
        return False

    # -- internals -------------------------------------------------------
    def _apply_grid_size(self) -> None:
        _, _, columns = GRID_SIZES[self._size]
        self.flow.set_min_children_per_line(2)
        self.flow.set_max_children_per_line(columns)

    def _sync_footer(self) -> None:
        if not self.show_footer:
            self.footer.set_visible(False)
            return
        self.footer.set_visible(True)
        self.footer_spinner.set_visible(self._loading)
        if self._loading:
            self.footer_spinner.start()
        else:
            self.footer_spinner.stop()
        self.footer_button.set_visible(
            self._has_more and not self._loading and len(self._posts) < MAX_POSTS
        )
        if self._loading:
            self.footer_label.set_visible(False)
        elif len(self._posts) >= MAX_POSTS:
            self.footer_label.set_text(f"Showing the first {MAX_POSTS} results")
            self.footer_label.set_visible(True)
        elif not self._has_more and self._posts:
            self.footer_label.set_text("You've reached the end")
            self.footer_label.set_visible(True)
        else:
            self.footer_label.set_visible(False)

    def _on_scrolled(self, adjustment) -> None:
        if not self.show_footer or self.on_load_more is None:
            return
        if self._loading or not self._has_more:
            return
        if len(self._posts) >= MAX_POSTS:
            return
        remaining = adjustment.get_upper() - adjustment.get_value() - adjustment.get_page_size()
        if remaining < 420:
            self._request_more()

    def _request_more(self, *_args) -> None:
        if self._loading or not self._has_more or self.on_load_more is None:
            return
        if len(self._posts) >= MAX_POSTS:
            return
        self.on_load_more()
