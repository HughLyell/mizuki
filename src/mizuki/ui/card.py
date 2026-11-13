"""Artwork tile used by the grids."""

from __future__ import annotations

import weakref

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from ..models import Post  # noqa: E402


class ImageCard(Gtk.Box):
    """A rounded artwork tile with hover actions."""

    def __init__(
        self,
        post: Post,
        loader,
        *,
        size: tuple[int, int],
        favorite: bool,
        blur_nsfw: bool,
        on_activate,
        on_download,
        on_favorite,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.post = post
        self.loader = loader
        self.on_activate = on_activate
        self.on_download = on_download
        self.on_favorite = on_favorite
        self._favorite = favorite
        self._loaded = False
        self._error_shown = False
        self._prefetch_timeout_id = 0
        self._width, self._height = size

        self.add_css_class("image-card")
        self.set_size_request(self._width, self._height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.START)
        self.set_focusable(True)
        self.set_tooltip_text(
            f"{post.source_name} · {post.dimensions_label}"
            + (f" · {post.artist}" if post.artist else "")
        )

        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)

        self.stack = Gtk.Stack()
        self.stack.add_css_class("card-stack")
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(160)
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        self._placeholder = self._build_placeholder()
        self.stack.add_named(self._placeholder, "placeholder")

        self.picture = Gtk.Picture()
        self.picture.add_css_class("card-picture")
        self.picture.set_content_fit(Gtk.ContentFit.COVER)
        self.picture.set_can_shrink(True)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_size_request(self._width, self._height)
        self.picture.set_overflow(Gtk.Overflow.HIDDEN)

        # A non-scrolling viewport clips oversized images and, importantly,
        # stops a tall image's natural size from inflating the gallery row.
        self._viewport = Gtk.ScrolledWindow()
        self._viewport.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self._viewport.set_propagate_natural_width(False)
        self._viewport.set_propagate_natural_height(False)
        self._viewport.set_child(self.picture)
        self._viewport.set_size_request(self._width, self._height)
        self._viewport.set_hexpand(True)
        self._viewport.set_vexpand(True)
        self._viewport.add_css_class("card-viewport")
        self._viewport.set_overflow(Gtk.Overflow.HIDDEN)
        self.stack.add_named(self._viewport, "image")
        self.stack.set_visible_child_name("placeholder")

        self.sensitive_cover: Gtk.Button | None = None
        self._blurred = bool(not post.is_safe and blur_nsfw)
        if self._blurred:
            self.sensitive_cover = self._build_sensitive_cover()
            self.stack.add_named(self.sensitive_cover, "sensitive")
            self.stack.set_visible_child_name("sensitive")

        overlay.set_child(self.stack)

        self.spinner = Gtk.Spinner()
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_size_request(24, 24)
        self.spinner.start()
        overlay.add_overlay(self.spinner)

        badge = self._build_rating_badge()
        if badge is not None:
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.START)
            badge.set_margin_start(8)
            badge.set_margin_top(8)
            overlay.add_overlay(badge)

        self._info = Gtk.Revealer()
        self._info.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._info.set_transition_duration(170)
        self._info.set_valign(Gtk.Align.END)
        self._info.set_halign(Gtk.Align.FILL)
        self._info.set_reveal_child(False)
        self._info.set_child(self._build_info_bar())
        overlay.add_overlay(self._info)

        self.append(overlay)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_pointer_enter)
        motion.connect("leave", self._on_pointer_leave)
        self.add_controller(motion)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_args: self._schedule_prefetch())
        focus.connect("leave", lambda *_args: self._cancel_prefetch())
        self.add_controller(focus)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_clicked)
        self.stack.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self.load()

    # -- construction ----------------------------------------------------
    def _build_placeholder(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("thumb-placeholder")
        box.set_valign(Gtk.Align.FILL)
        box.set_halign(Gtk.Align.FILL)
        box.set_size_request(self._width, self._height)
        icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        icon.set_pixel_size(30)
        icon.add_css_class("thumb-placeholder-icon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_vexpand(True)
        box.append(icon)
        return box

    def _build_sensitive_cover(self) -> Gtk.Button:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        icon.set_pixel_size(26)
        label = Gtk.Label(label="Sensitive")
        box.append(icon)
        box.append(label)
        button = Gtk.Button()
        button.add_css_class("sensitive-cover")
        button.set_child(box)
        button.set_hexpand(True)
        button.set_vexpand(True)
        button.set_tooltip_text("Sensitive artwork — click to view")
        button.connect("clicked", lambda *_: self.on_activate(self.post))
        return button

    def _build_rating_badge(self) -> Gtk.Widget | None:
        if self.post.is_safe:
            return None
        label = Gtk.Label(label="NSFW" if self.post.rating == "explicit" else "SENSITIVE")
        label.add_css_class("rating-badge")
        if self.post.rating == "explicit":
            label.add_css_class("nsfw")
        return label

    def _build_info_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.add_css_class("card-overlay")
        bar.set_valign(Gtk.Align.END)
        bar.set_halign(Gtk.Align.FILL)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_valign(Gtk.Align.CENTER)
        source = Gtk.Label(label=self.post.source_name)
        source.add_css_class("card-source")
        source.set_xalign(0)
        source.set_ellipsize(Pango.EllipsizeMode.END)
        meta_text = self.post.dimensions_label
        if self.post.artist:
            meta_text = f"{self.post.artist} · {meta_text}"
        meta = Gtk.Label(label=meta_text)
        meta.add_css_class("card-meta")
        meta.set_xalign(0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_max_width_chars(24)
        text.append(source)
        text.append(meta)
        text.set_hexpand(True)
        bar.append(text)

        self._favorite_button = Gtk.Button()
        self._favorite_button.add_css_class("card-action")
        self._favorite_button.set_tooltip_text("Add to favourites")
        self._favorite_button.connect("clicked", self._on_favorite_clicked)
        self._sync_favorite_button()
        bar.append(self._favorite_button)

        download = Gtk.Button(icon_name="folder-download-symbolic")
        download.add_css_class("card-action")
        download.set_tooltip_text("Download artwork")
        download.connect("clicked", lambda *_: self.on_download(self.post))
        bar.append(download)
        return bar

    # -- public ----------------------------------------------------------
    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.post.thumb_url:
            self._show_load_error()
            return
        # The loader may outlive this card (grids are cleared on every new
        # search), so register a weak callback instead of a bound method.
        self_ref = weakref.ref(self)

        def _deliver(texture, ref=self_ref) -> None:
            card = ref()
            if card is not None:
                card._on_texture(texture)

        self.loader.load(
            self.post.thumb_url,
            _deliver,
            referer=self.post.referer,
        )

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = bool(favorite)
        self._sync_favorite_button()

    def set_tile_size(self, width: int, height: int) -> None:
        self._width, self._height = width, height
        self.set_size_request(width, height)
        self.picture.set_size_request(width, height)
        self._viewport.set_size_request(width, height)
        self._placeholder.set_size_request(width, height)

    def set_blurred(self, blurred: bool) -> None:
        self._blurred = bool(blurred and not self.post.is_safe)
        if self._blurred and self.sensitive_cover is None:
            self.sensitive_cover = self._build_sensitive_cover()
            self.stack.add_named(self.sensitive_cover, "sensitive")
        self._refresh_visible_page()

    def _refresh_visible_page(self) -> None:
        if self._blurred and self.sensitive_cover is not None:
            self.stack.set_visible_child_name("sensitive")
        elif self._error_shown:
            self.stack.set_visible_child_name("error")
        elif self.picture.get_paintable() is not None:
            self.stack.set_visible_child_name("image")
        else:
            self.stack.set_visible_child_name("placeholder")

    # -- callbacks -------------------------------------------------------
    def _on_texture(self, texture) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        if texture is not None:
            self.picture.set_paintable(texture)
            self._refresh_visible_page()
        else:
            self._show_load_error()

    def _show_load_error(self) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        if self._error_shown:
            return
        self._error_shown = True
        if self.stack.get_child_by_name("error") is None:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name("image-missing-symbolic")
            icon.set_pixel_size(28)
            label = Gtk.Label(label="Preview unavailable")
            label.add_css_class("dim-label")
            box.append(icon)
            box.append(label)
            self.stack.add_named(box, "error")
        self._refresh_visible_page()

    def _on_pointer_enter(self, *_args) -> None:
        self._info.set_reveal_child(True)
        self.set_cursor_from_name("pointer")
        self._schedule_prefetch()

    def _on_pointer_leave(self, *_args) -> None:
        self._info.set_reveal_child(False)
        self.set_cursor_from_name("default")
        self._cancel_prefetch()

    def _schedule_prefetch(self) -> None:
        if not self.post.preview_url or self.post.preview_url == self.post.thumb_url:
            return
        if self.loader.cached(self.post.preview_url) is not None:
            return
        self._cancel_prefetch()
        self._prefetch_timeout_id = GLib.timeout_add(180, self._prefetch_preview)

    def _prefetch_preview(self):
        self._prefetch_timeout_id = 0
        if self.post.preview_url:
            self.loader.prefetch(
                self.post.preview_url,
                referer=self.post.referer,
                store_disk=True,
            )
        return False

    def _cancel_prefetch(self) -> None:
        if self._prefetch_timeout_id:
            GLib.source_remove(self._prefetch_timeout_id)
            self._prefetch_timeout_id = 0

    def _on_clicked(self, gesture, _n_press, _x, _y) -> None:
        if gesture.get_button() == 1:
            self._cancel_prefetch()
            self.on_activate(self.post)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._cancel_prefetch()
            self.on_activate(self.post)
            return True
        return False

    def _on_favorite_clicked(self, _button) -> None:
        self._favorite = bool(self.on_favorite(self.post))
        self._sync_favorite_button()

    def _sync_favorite_button(self) -> None:
        icon = "starred-symbolic" if self._favorite else "non-starred-symbolic"
        self._favorite_button.set_icon_name(icon)
        self._favorite_button.set_tooltip_text(
            "Remove from favourites" if self._favorite else "Add to favourites"
        )
