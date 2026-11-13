"""Responsive artwork grid with progressive loading."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..models import Post  # noqa: E402
from .card import ImageCard  # noqa: E402
from .gallery_layout import build_justified_rows  # noqa: E402

log = logging.getLogger(__name__)

GALLERY_GAP = 12
GRID_SIDE_MARGIN = 24
GRID_HORIZONTAL_MARGIN = GRID_SIDE_MARGIN * 2
# .image-card has a 1px border on each side; size requests exclude CSS chrome.
CARD_CHROME = 2

# target row height, minimum row height, maximum row height
GRID_SIZES: dict[str, tuple[int, int, int]] = {
    "compact": (180, 135, 235),
    "comfortable": (235, 170, 300),
    "large": (330, 245, 430),
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
        self._max_width = max_width
        self._cards: dict[str, ImageCard] = {}
        self._posts: list[Post] = []
        self._rows: list[Gtk.Box] = []
        self._layout_width = 0
        self._allocated_width = 0
        self._relayout_timer_id = 0
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
        outer.set_margin_start(GRID_SIDE_MARGIN)
        outer.set_margin_end(GRID_SIDE_MARGIN)

        self.flow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GALLERY_GAP)
        self.flow.add_css_class("image-grid")
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_halign(Gtk.Align.FILL)
        self.flow.set_hexpand(True)
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
        self.connect("map", lambda *_: GLib.idle_add(self._relayout_check))

    # -- public API ------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self._posts)

    @property
    def posts(self) -> list[Post]:
        return list(self._posts)

    def clear(self) -> None:
        self._clear_rows()
        self._cards.clear()
        self._posts.clear()
        self._layout_width = 0
        self._loading = False
        self._has_more = False
        self._sync_footer()
        self.scroll_to_top()

    def append(self, posts: list[Post]) -> list[ImageCard]:
        added: list[ImageCard] = []
        favorite_keys = self.favorites.keys()
        blurred = bool(self.settings.get("blur_nsfw", True))
        target_height, _, _ = GRID_SIZES[self._size]
        for post in posts:
            if post.key in self._cards or len(self._posts) >= MAX_POSTS:
                continue
            tile_width = max(48, int(round(post.aspect_ratio * target_height)))
            card = ImageCard(
                post,
                self.loader,
                size=(tile_width, target_height),
                favorite=post.key in favorite_keys,
                blur_nsfw=blurred,
                on_activate=self.on_activate,
                on_download=self.on_download,
                on_favorite=self.on_favorite,
            )
            card.set_hexpand(False)
            card.set_halign(Gtk.Align.START)
            card.set_valign(Gtk.Align.FILL)
            self._cards[post.key] = card
            self._posts.append(post)
            added.append(card)
        if added:
            self._relayout(force=True)
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
        self._relayout(force=True)

    def request_relayout(self) -> None:
        self._relayout(force=True)

    def prepare_for_width(self, allocated_width: int) -> None:
        """Shrink tiles before the sidebar expands into the content area.

        ``allocated_width`` is the PostGrid's future allocated width. The cards'
        size requests are part of the content's minimum width, so if the sidebar
        is revealed first, the window can grow instead of letting the gallery
        shrink. The internal horizontal margin is subtracted here to keep the
        grid's minimum size consistent with its reflow width.
        """
        if allocated_width > 0:
            content_width = max(240, int(allocated_width) - GRID_HORIZONTAL_MARGIN)
            self._relayout(content_width, force=True)

    def refresh_favorites(self) -> None:
        favorite_keys = self.favorites.keys()
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
        self._relayout(force=True)

    def _relayout(self, width: int | None = None, *, force: bool = False) -> None:
        if width is None:
            width = self._available_width()
        width = max(240, int(width))
        if not force and width == self._layout_width:
            return
        target, minimum, maximum = GRID_SIZES[self._size]
        rows = build_justified_rows(
            self._posts,
            width,
            target,
            gap=GALLERY_GAP,
            min_height=minimum,
            max_height=maximum,
            chrome=CARD_CHROME,
        )

        self._clear_rows()
        for row_layout in rows:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=GALLERY_GAP)
            row.add_css_class("gallery-row")
            row.set_halign(Gtk.Align.START)
            row.set_valign(Gtk.Align.START)
            for tile in row_layout.tiles:
                card = self._cards.get(tile.post.key)
                if card is None:
                    continue
                card.set_tile_size(tile.width, tile.height)
                card.set_hexpand(False)
                card.set_halign(Gtk.Align.START)
                card.set_valign(Gtk.Align.FILL)
                row.append(card)
            self.flow.append(row)
            self._rows.append(row)
        self._layout_width = width

    def _clear_rows(self) -> None:
        for card in self._cards.values():
            if card.get_parent() is not None:
                card.unparent()
        for row in self._rows:
            if row.get_parent() is self.flow:
                self.flow.remove(row)
        self._rows.clear()

    def _available_width(self) -> int:
        # Use the ScrolledWindow's allocated width rather than the child grid's
        # width. Reading the child width can create a layout feedback loop as
        # the justified rows request more natural width than the viewport.
        outer_width = self.get_width()
        if outer_width <= 0:
            return 1100
        return max(240, min(self._max_width, outer_width - GRID_HORIZONTAL_MARGIN))

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        if width == self._allocated_width:
            return
        self._allocated_width = width
        # Debounce resize storms: a window drag delivers many allocations, and
        # a full justified relayout re-parents every card.
        if self._relayout_timer_id:
            GLib.source_remove(self._relayout_timer_id)
        self._relayout_timer_id = GLib.timeout_add(50, self._relayout_after_resize)

    def _relayout_after_resize(self):
        self._relayout_timer_id = 0
        self._relayout_check()
        return False

    def _relayout_check(self):
        width = self._available_width()
        if width > 0 and abs(width - self._layout_width) >= 2:
            log.debug("Reflowing gallery at width %s (was %s)", width, self._layout_width)
            self._relayout(width)
        return False

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
