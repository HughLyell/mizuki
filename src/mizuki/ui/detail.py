"""Full artwork detail dialog."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..models import Post  # noqa: E402


class ImageDetailDialog(Adw.Dialog):
    def __init__(
        self,
        post: Post,
        loader,
        favorites,
        *,
        on_download,
        on_favorite,
        on_tag_activate,
    ) -> None:
        super().__init__()
        self.post = post
        self.loader = loader
        self.favorites = favorites
        self.on_download = on_download
        self.on_favorite = on_favorite
        self.on_tag_activate = on_tag_activate
        self._preview_attempts = 0
        self._thumbnail_texture = None

        self.set_content_width(1140)
        self.set_content_height(760)

        root = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)

        close_button = Gtk.Button(icon_name="window-close-symbolic")
        close_button.add_css_class("flat")
        close_button.set_tooltip_text("Close")
        close_button.connect("clicked", lambda *_: self.close())
        header.pack_start(close_button)

        window_title = Adw.WindowTitle(title=f"Post #{post.id}", subtitle=post.source_name)
        header.set_title_widget(window_title)

        self.favorite_button = Gtk.Button()
        self.favorite_button.add_css_class("flat")
        self.favorite_button.connect("clicked", self._toggle_favorite)
        self._sync_favorite_button()
        header.pack_end(self.favorite_button)

        download_button = Gtk.Button()
        download_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        download_box.set_halign(Gtk.Align.CENTER)
        download_icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        download_icon.set_pixel_size(15)
        download_box.append(download_icon)
        download_box.append(Gtk.Label(label="Download"))
        download_button.set_child(download_box)
        download_button.add_css_class("suggested-action")
        download_button.add_css_class("pill-button")
        download_button.connect("clicked", self._download)
        header.pack_end(download_button)
        root.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        content.set_hexpand(True)
        content.set_vexpand(True)
        content.append(self._build_stage())
        content.append(self._build_info_panel())
        root.set_content(content)

        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(root)
        self.set_child(self.toasts)

        self._seed_thumbnail()
        self._load_preview()

    # -- construction ----------------------------------------------------
    def _build_stage(self) -> Gtk.Widget:
        stage = Gtk.Overlay()
        stage.add_css_class("image-stage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)

        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        stage.set_child(self.picture)

        self.spinner = Gtk.Spinner()
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_size_request(36, 36)
        self.spinner.start()
        stage.add_overlay(self.spinner)

        self.stage_error = Gtk.Label(label="")
        self.stage_error.add_css_class("dim-label")
        self.stage_error.set_halign(Gtk.Align.CENTER)
        self.stage_error.set_valign(Gtk.Align.CENTER)
        self.stage_error.set_wrap(True)
        self.stage_error.set_visible(False)
        stage.add_overlay(self.stage_error)
        return stage

    def _build_info_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("detail-panel")
        panel.set_size_request(350, -1)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_width(False)
        scroll.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        inner.set_margin_top(22)
        inner.set_margin_bottom(26)
        inner.set_margin_start(22)
        inner.set_margin_end(22)

        title = Gtk.Label(label=self.post.artist or "Untitled artwork")
        title.add_css_class("detail-title")
        title.set_xalign(0)
        title.set_wrap(True)
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        inner.append(title)

        subtitle = Gtk.Label(
            label=f"Post #{self.post.id} · {self.post.source_name}"
        )
        subtitle.add_css_class("detail-subtitle")
        subtitle.set_xalign(0)
        inner.append(subtitle)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rating = Gtk.Label(label=self.post.rating_label)
        rating.add_css_class("tag-chip")
        if not self.post.is_safe:
            rating.add_css_class("nsfw")
        badges.append(rating)
        dimensions = Gtk.Label(label=self.post.dimensions_label)
        dimensions.add_css_class("tag-chip")
        badges.append(dimensions)
        if self.post.score:
            score = Gtk.Label(label=f"♥ {self.post.score}")
            score.add_css_class("tag-chip")
            badges.append(score)
        inner.append(badges)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        copy_button = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_button.set_tooltip_text("Copy image link")
        copy_button.add_css_class("pill-button")
        copy_button.connect("clicked", self._copy_link)
        actions.append(copy_button)

        open_button = Gtk.Button(icon_name="web-browser-symbolic")
        open_button.set_tooltip_text("Open the source page")
        open_button.add_css_class("pill-button")
        open_button.connect("clicked", self._open_page)
        actions.append(open_button)
        inner.append(actions)

        inner.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        tags_heading = Gtk.Label(label="TAGS")
        tags_heading.add_css_class("detail-section-title")
        tags_heading.set_xalign(0)
        inner.append(tags_heading)

        if self.post.tags:
            tags = Gtk.FlowBox()
            tags.set_selection_mode(Gtk.SelectionMode.NONE)
            tags.set_min_children_per_line(1)
            tags.set_max_children_per_line(4)
            tags.set_row_spacing(6)
            tags.set_column_spacing(6)
            tags.set_homogeneous(False)
            tags.set_valign(Gtk.Align.START)
            for tag in self.post.tags[:80]:
                chip = Gtk.Button(label=tag)
                chip.add_css_class("tag-chip")
                chip.set_tooltip_text(f"Search for “{tag}”")
                chip.connect("clicked", self._activate_tag, tag)
                tags.append(chip)
            inner.append(tags)
        else:
            none = Gtk.Label(label="No tags were returned by this source.")
            none.add_css_class("dim-label")
            none.set_xalign(0)
            none.set_wrap(True)
            inner.append(none)

        inner.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        details_heading = Gtk.Label(label="DETAILS")
        details_heading.add_css_class("detail-section-title")
        details_heading.set_xalign(0)
        inner.append(details_heading)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        details.append(self._meta_row("Source", self.post.source_name))
        if self.post.artist:
            details.append(self._meta_row("Artist", self.post.artist))
        details.append(self._meta_row("Dimensions", self.post.dimensions_label))
        details.append(self._meta_row("Rating", self.post.rating_label))
        details.append(self._meta_row("Format", self.post.ext.upper()))
        details.append(self._meta_row("Post ID", self.post.id))
        inner.append(details)

        note = Gtk.Label(
            label="Artwork belongs to the original artist. Open the source page to support them."
        )
        note.add_css_class("dim-label")
        note.set_xalign(0)
        note.set_wrap(True)
        inner.append(note)

        scroll.set_child(inner)
        panel.append(scroll)
        return panel

    @staticmethod
    def _meta_row(key: str, value: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        key_label = Gtk.Label(label=key)
        key_label.add_css_class("meta-key")
        key_label.set_xalign(0)
        value_label = Gtk.Label(label=value)
        value_label.add_css_class("meta-value")
        value_label.set_xalign(0)
        value_label.set_wrap(True)
        value_label.set_selectable(True)
        row.append(key_label)
        row.append(value_label)
        return row

    # -- data ------------------------------------------------------------
    def _seed_thumbnail(self) -> None:
        if not self.post.thumb_url:
            return
        texture = self.loader.cached(self.post.thumb_url)
        if texture is not None:
            self._thumbnail_texture = texture
            self._show_texture(texture)

    def _load_preview(self) -> None:
        url = self.post.preview_url or self.post.file_url or self.post.thumb_url
        if not url:
            self._preview_failed("No preview is available.")
            return
        self.loader.load(
            url,
            self._on_preview,
            referer=self.post.referer,
            store_disk=True,
        )

    def _show_texture(self, texture) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.stage_error.set_visible(False)
        self.picture.set_paintable(texture)

    def _on_preview(self, texture) -> None:
        if texture is not None:
            self._show_texture(texture)
            return
        self._preview_attempts += 1
        if (
            self._preview_attempts == 1
            and self._thumbnail_texture is None
            and self.post.thumb_url
            and self.post.thumb_url != self.post.preview_url
        ):
            self.loader.load(
                self.post.thumb_url,
                self._on_preview,
                referer=self.post.referer,
                store_disk=True,
            )
            return
        if self._thumbnail_texture is not None:
            # Keep the instant thumbnail visible instead of replacing it with
            # an error message when the larger preview cannot be fetched.
            self.spinner.stop()
            self.spinner.set_visible(False)
            return
        self._preview_failed("The full preview could not be loaded. Try opening the source page.")

    def _preview_failed(self, message: str) -> None:
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.stage_error.set_text(message)
        self.stage_error.set_visible(True)

    # -- actions ---------------------------------------------------------
    def _download(self, _button) -> None:
        try:
            self.on_download(self.post)
        except Exception as exc:
            self._toast(f"Could not download: {exc}")

    def _toggle_favorite(self, _button) -> None:
        self.on_favorite(self.post)
        self._sync_favorite_button()

    def _sync_favorite_button(self) -> None:
        favorite = self.favorites.contains(self.post)
        self.favorite_button.set_icon_name(
            "starred-symbolic" if favorite else "non-starred-symbolic"
        )
        self.favorite_button.set_tooltip_text(
            "Remove from favourites" if favorite else "Add to favourites"
        )

    def _copy_link(self, _button) -> None:
        url = self.post.file_url or self.post.page_url
        display = Gdk.Display.get_default()
        if display is None or not url:
            self._toast("No link available")
            return
        display.get_clipboard().set_text(url)
        self._toast("Image link copied")

    def _open_page(self, _button) -> None:
        if not self.post.page_url:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(self.post.page_url, None)
        except GLib.Error as exc:
            self._toast(f"Could not open browser: {exc.message}")

    def _activate_tag(self, _button, tag: str) -> None:
        self.close()
        self.on_tag_activate(tag)

    def _toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast.new(message))
