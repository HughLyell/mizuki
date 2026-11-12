"""Main application window."""

from __future__ import annotations

import logging
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import APP_ID, APP_ISSUES, APP_NAME, APP_WEBSITE, __version__  # noqa: E402
from ..models import Post  # noqa: E402
from ..sources import SourceRegistry  # noqa: E402
from .detail import ImageDetailDialog  # noqa: E402
from .discover import DiscoverPage  # noqa: E402
from .pages import DownloadsPage, FavoritesPage  # noqa: E402
from .preferences import PreferencesDialog  # noqa: E402
from .theme import ACCENTS  # noqa: E402

log = logging.getLogger(__name__)

PAGE_TITLES = {
    "discover": "Browse anime artwork",
    "favorites": "Your saved artwork",
    "downloads": "Your download queue",
}


class MizukiWindow(Adw.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app)
        self.app = app
        self.settings = app.settings
        self.registry: SourceRegistry = app.registry
        self.favorites = app.favorites
        self.downloads = app.downloads
        self.loader = app.loader
        self.search_controller = app.search_controller
        self.theme = app.theme
        self._current_page = "discover"
        self._syncing_safe = False
        self._syncing_sidebar = False
        self._sidebar_visible = bool(self.settings.get("sidebar_visible", True))
        self._last_sidebar_width = 272
        self._source_selection_guard = False
        self._changing_source = False
        self._preferences: PreferencesDialog | None = None
        self._dialogs: set[ImageDetailDialog] = set()
        self._settings_subscriptions: list[tuple[str, object]] = []

        self.set_title(APP_NAME)
        self.set_default_size(1280, 840)
        self.set_size_request(1000, 620)
        self.add_css_class("mizuki-window")

        self.toast_overlay = Adw.ToastOverlay()
        self.toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(self.toolbar)
        self.set_content(self.toast_overlay)

        self.toolbar.add_top_bar(self._build_header())
        self.toolbar.set_content(self._build_body())
        self._build_pages()
        self._build_sidebar_footer()

        self._initial_source = self._determine_initial_source()
        self._rebuild_source_rows()
        self._sync_source_selection(self._initial_source)
        self._update_sidebar_recent()
        self._apply_sidebar_visibility(self._sidebar_visible)
        self._sync_safe_controls(bool(self.settings.get("safe_search", True)))
        self._apply_color_scheme()
        self._apply_accent()
        self._update_header()

        self.settings.subscribe("safe_search", self._on_safe_setting_changed)
        self.settings.subscribe("blur_nsfw", self._on_blur_changed)
        self.settings.subscribe("sidebar_visible", self._on_sidebar_setting_changed)
        self.settings.subscribe("recent_searches", self._on_recent_searches_changed)
        self.settings.subscribe("grid_size", self._on_grid_changed)
        self.settings.subscribe("accent", self._on_accent_changed)
        self.settings.subscribe("color_scheme", self._on_scheme_changed)
        self.settings.subscribe("enabled_sources", self._on_sources_changed)
        self._settings_subscriptions = [
            ("safe_search", self._on_safe_setting_changed),
            ("blur_nsfw", self._on_blur_changed),
            ("sidebar_visible", self._on_sidebar_setting_changed),
            ("recent_searches", self._on_recent_searches_changed),
            ("grid_size", self._on_grid_changed),
            ("accent", self._on_accent_changed),
            ("color_scheme", self._on_scheme_changed),
            ("enabled_sources", self._on_sources_changed),
        ]

        self.downloads.add_listener(self._on_download_event)
        style_manager = Adw.StyleManager.get_default()
        self._dark_handler = style_manager.connect("notify::dark", self._on_dark_changed)

        self.switch_page("discover", refresh=False)
        self._update_download_badge()
        self.connect("close-request", self._on_close_request)
        if os.environ.get("MIZUKI_SMOKE_TEST") != "1":
            GLib.idle_add(self._initial_load)

    # -- shell -----------------------------------------------------------
    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.add_css_class("mizuki-header")
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(True)

        self.sidebar_toggle = Gtk.ToggleButton()
        self.sidebar_toggle.set_icon_name("sidebar-show-symbolic")
        self.sidebar_toggle.add_css_class("flat")
        self.sidebar_toggle.add_css_class("sidebar-toggle")
        self.sidebar_toggle.set_active(self._sidebar_visible)
        self.sidebar_toggle.set_tooltip_text(
            "Hide sidebar" if self._sidebar_visible else "Show sidebar"
        )
        self.sidebar_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Hide sidebar" if self._sidebar_visible else "Show sidebar"],
        )
        self.sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        header.pack_start(self.sidebar_toggle)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        brand.add_css_class("brand-box")
        icon = Gtk.Image.new_from_icon_name(APP_ID)
        icon.set_pixel_size(30)
        brand.append(icon)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("brand-title")
        title.set_xalign(0)
        self.brand_subtitle = Gtk.Label(label=PAGE_TITLES["discover"])
        self.brand_subtitle.add_css_class("brand-subtitle")
        self.brand_subtitle.set_xalign(0)
        titles.append(title)
        titles.append(self.brand_subtitle)
        brand.append(titles)
        header.set_title_widget(brand)

        downloads_button = Gtk.Button(icon_name="folder-download-symbolic")
        downloads_button.add_css_class("flat")
        downloads_button.set_tooltip_text("Downloads")
        downloads_button.connect("clicked", lambda *_: self.switch_page("downloads"))
        header.pack_end(downloads_button)

        self.header_safe = Gtk.ToggleButton()
        self.header_safe.add_css_class("sfw-pill")
        safe_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        safe_box.set_halign(Gtk.Align.CENTER)
        shield = Gtk.Image.new_from_icon_name("security-high-symbolic")
        shield.set_pixel_size(14)
        safe_box.append(shield)
        safe_box.append(Gtk.Label(label="SFW"))
        self.header_safe.set_child(safe_box)
        self.header_safe.connect("toggled", self._on_header_safe_toggled)
        header.pack_end(self.header_safe)

        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About Mizuki", "app.about")
        menu.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.add_css_class("flat")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("Main menu")
        header.pack_end(menu_button)

        return header

    def _build_body(self) -> Gtk.Widget:
        # A plain horizontal box makes the sidebar and content share one fixed
        # window width. Adw.OverlaySplitView could grow the window when the
        # sidebar was revealed, pushing the opposite edge off-screen.
        self.split_view = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.split_view.set_hexpand(True)
        self.split_view.set_vexpand(True)

        self.page_stack = Gtk.Stack()
        self.page_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.page_stack.set_transition_duration(140)
        self.page_stack.set_vexpand(True)
        self.page_stack.set_hexpand(True)

        self.sidebar_widget = self._build_sidebar()
        self.sidebar_widget.set_visible(self._sidebar_visible)
        self.split_view.append(self.sidebar_widget)
        self.split_view.append(self.page_stack)
        return self.split_view

    def _build_sidebar(self) -> Gtk.Widget:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sidebar.add_css_class("sidebar")
        sidebar.add_css_class("sidebar-pane")
        sidebar.set_size_request(272, -1)

        scroll = Gtk.ScrolledWindow()
        scroll.add_css_class("sidebar-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_propagate_natural_width(False)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_margin_top(8)
        inner.set_margin_bottom(12)

        inner.append(self._heading("BROWSE"))
        self.nav_list = Gtk.ListBox()
        self.nav_list.add_css_class("navigation-sidebar")
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.connect("row-activated", self._on_nav_activated)
        self._nav_rows: dict[str, Adw.ActionRow] = {}
        self._nav_wrappers: dict[str, Gtk.ListBoxRow] = {}
        self._nav_rows["discover"] = self._make_nav_row(
            "discover", "Discover", "system-search-symbolic"
        )
        self._nav_rows["favorites"] = self._make_nav_row(
            "favorites", "Favourites", "starred-symbolic"
        )
        downloads_row, badge = self._make_nav_row(
            "downloads", "Downloads", "folder-download-symbolic", with_badge=True
        )
        self._nav_rows["downloads"] = downloads_row
        self._download_badge = badge
        for index, (page_id, row) in enumerate(self._nav_rows.items()):
            self.nav_list.append(row)
            wrapper = self.nav_list.get_row_at_index(index)
            if isinstance(wrapper, Gtk.ListBoxRow):
                wrapper._mizuki_page = page_id  # type: ignore[attr-defined]
                self._nav_wrappers[page_id] = wrapper
        inner.append(self.nav_list)

        inner.append(self._heading("RECENT SEARCHES"))
        self.sidebar_recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sidebar_recent_box.set_margin_start(14)
        self.sidebar_recent_box.set_margin_end(14)
        self.sidebar_recent_box.set_margin_top(2)
        self.sidebar_recent_box.set_margin_bottom(4)

        self.sidebar_recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sidebar_recent.set_halign(Gtk.Align.FILL)
        self.sidebar_recent.set_valign(Gtk.Align.START)
        self.sidebar_recent_box.append(self.sidebar_recent)

        self.sidebar_recent_empty = Gtk.Label(label="No recent searches yet")
        self.sidebar_recent_empty.add_css_class("sidebar-empty")
        self.sidebar_recent_empty.set_xalign(0)
        self.sidebar_recent_box.append(self.sidebar_recent_empty)
        inner.append(self.sidebar_recent_box)

        inner.append(self._heading("SOURCES"))
        self.source_list = Gtk.ListBox()
        self.source_list.add_css_class("navigation-sidebar")
        self.source_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.source_list.connect("row-selected", self._on_source_row_activated)
        self._source_rows: dict[str, Adw.ActionRow] = {}
        inner.append(self.source_list)

        scroll.set_child(inner)
        sidebar.append(scroll)
        self.sidebar_footer_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.append(self.sidebar_footer_slot)
        return sidebar

    def _build_sidebar_footer(self) -> None:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        footer.add_css_class("sidebar-footer")

        safe_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        shield = Gtk.Image.new_from_icon_name("security-high-symbolic")
        shield.set_pixel_size(17)
        shield.set_valign(Gtk.Align.CENTER)
        safe_row.append(shield)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="Safe search")
        title.add_css_class("safe-title")
        title.set_xalign(0)
        caption = Gtk.Label(label="Hide adult artwork")
        caption.add_css_class("safe-caption")
        caption.set_xalign(0)
        labels.append(title)
        labels.append(caption)
        labels.set_hexpand(True)
        safe_row.append(labels)
        self.safe_switch = Gtk.Switch()
        self.safe_switch.set_valign(Gtk.Align.CENTER)
        self.safe_switch.set_active(bool(self.settings.get("safe_search", True)))
        self.safe_switch.connect("notify::active", self._on_sidebar_safe_toggled)
        safe_row.append(self.safe_switch)
        footer.append(safe_row)

        preferences = Gtk.Button()
        preferences.add_css_class("flat")
        pref_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        pref_box.set_halign(Gtk.Align.START)
        gear = Gtk.Image.new_from_icon_name("preferences-system-symbolic")
        gear.set_pixel_size(15)
        pref_box.append(gear)
        pref_box.append(Gtk.Label(label="Preferences"))
        preferences.set_child(pref_box)
        preferences.connect("clicked", lambda *_: self.app.activate_action("preferences", None))
        footer.append(preferences)

        self.sidebar_footer_slot.append(footer)

    def _build_pages(self) -> None:
        self.discover = DiscoverPage(
            self.settings,
            self.registry,
            self.loader,
            self.favorites,
            self.search_controller,
            on_open_post=self._open_post,
            on_download=self._download_post,
            on_favorite=self._toggle_favorite,
            on_source_changed=lambda source_id: self._on_source_selected(source_id, run=False),
            on_request_safe=self.request_safe_search,
            on_warning=self.toast,
        )
        self.page_stack.add_named(self.discover, "discover")

        self.favorites_page = FavoritesPage(
            self.loader,
            self.settings,
            self.favorites,
            on_activate=self._open_post,
            on_download=self._download_post,
            on_favorite=self._toggle_favorite,
            on_discover=lambda: self.switch_page("discover"),
        )
        self.page_stack.add_named(self.favorites_page, "favorites")

        self.downloads_page = DownloadsPage(
            self.settings,
            self.downloads,
            on_discover=lambda: self.switch_page("discover"),
            on_notice=self.toast,
        )
        self.page_stack.add_named(self.downloads_page, "downloads")

    # -- sidebar helpers -------------------------------------------------
    @staticmethod
    def _heading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("sidebar-heading")
        label.set_xalign(0)
        return label

    def _make_nav_row(self, page_id: str, title: str, icon: str, *, with_badge: bool = False):
        action = Adw.ActionRow()
        action.set_title(title)
        image = Gtk.Image.new_from_icon_name(icon)
        image.set_pixel_size(17)
        action.add_prefix(image)
        action._mizuki_page = page_id  # type: ignore[attr-defined]
        badge = None
        if with_badge:
            badge = Gtk.Label(label="")
            badge.add_css_class("badge-count")
            badge.set_valign(Gtk.Align.CENTER)
            badge.set_visible(False)
            action.add_suffix(badge)
        return (action, badge) if with_badge else action

    @staticmethod
    def _row_marker(row, attribute: str):
        marker = getattr(row, attribute, None)
        if marker is not None:
            return marker
        child = row.get_child() if isinstance(row, Gtk.ListBoxRow) else None
        return getattr(child, attribute, None) if child is not None else None

    def _on_nav_activated(self, _listbox, row) -> None:
        page_id = self._row_marker(row, "_mizuki_page")
        if page_id:
            self.switch_page(page_id)

    def _rebuild_source_rows(self) -> None:
        child = self.source_list.get_first_child()
        while child is not None:
            self.source_list.remove(child)
            child = self.source_list.get_first_child()
        self._source_rows.clear()

        all_row = Adw.ActionRow()
        all_row.set_title("All sources")
        image = Gtk.Image.new_from_icon_name("system-search-symbolic")
        image.set_pixel_size(17)
        all_row.add_prefix(image)
        all_row._mizuki_source = "all"  # type: ignore[attr-defined]
        self.source_list.append(all_row)
        all_wrapper = self.source_list.get_row_at_index(0)
        if isinstance(all_wrapper, Gtk.ListBoxRow):
            all_wrapper._mizuki_source = "all"  # type: ignore[attr-defined]
            self._source_rows["all"] = all_wrapper

        enabled = set(self.settings.get("enabled_sources", []))
        index = 1
        for source in self.registry.all():
            if source.id not in enabled:
                continue
            row = Adw.ActionRow()
            row.set_title(source.name)
            icon = Gtk.Image.new_from_icon_name(source.icon)
            icon.set_pixel_size(17)
            row.add_prefix(icon)
            row._mizuki_source = source.id  # type: ignore[attr-defined]
            self.source_list.append(row)
            wrapper = self.source_list.get_row_at_index(index)
            index += 1
            if isinstance(wrapper, Gtk.ListBoxRow):
                wrapper._mizuki_source = source.id  # type: ignore[attr-defined]
                self._source_rows[source.id] = wrapper
        self._sync_source_selection(self.discover.current_source_id() if hasattr(self, "discover") else "all")

    def _on_source_row_activated(self, _listbox, row) -> None:
        if self._source_selection_guard or row is None:
            return
        source_id = self._row_marker(row, "_mizuki_source")
        if source_id:
            self._on_source_selected(source_id, run=True)

    def _sync_source_selection(self, source_id: str) -> None:
        row = self._source_rows.get(source_id)
        if row is not None:
            self._source_selection_guard = True
            try:
                self.source_list.select_row(row)
            finally:
                self._source_selection_guard = False
            log.debug(
                "Selected source %s (row=%r, selected=%r)",
                source_id, row, self.source_list.get_selected_row(),
            )

    def _update_sidebar_recent(self) -> None:
        child = self.sidebar_recent.get_first_child()
        while child is not None:
            self.sidebar_recent.remove(child)
            child = self.sidebar_recent.get_first_child()

        recent = [
            " ".join(str(item).split())
            for item in self.settings.get("recent_searches", [])
            if str(item).strip()
        ][:6]
        for start in range(0, len(recent), 2):
            pair = recent[start:start + 2]
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.set_homogeneous(True)
            for term in pair:
                chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                chip.add_css_class("recent-chip")
                chip.set_hexpand(True)

                label = Gtk.Label(label=term)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_width_chars(11)
                label.set_max_width_chars(11)
                label.set_xalign(0)
                search = Gtk.Button()
                search.add_css_class("flat")
                search.add_css_class("recent-chip-search")
                search.set_child(label)
                search.set_tooltip_text(f"Search for “{term}”")
                search.connect("clicked", lambda _b, value=term: self._search_tag(value))
                chip.append(search)

                close = Gtk.Button(icon_name="window-close-symbolic")
                close.add_css_class("flat")
                close.add_css_class("recent-chip-close")
                close.set_tooltip_text(f"Remove “{term}” from recent searches")
                close.update_property(
                    [Gtk.AccessibleProperty.LABEL],
                    [f"Remove {term} from recent searches"],
                )
                close.connect(
                    "clicked", lambda _b, value=term: self._remove_recent_search(value)
                )
                chip.append(close)
                row.append(chip)
            if len(pair) == 1:
                row.append(Gtk.Box())
            self.sidebar_recent.append(row)

        self.sidebar_recent.set_visible(bool(recent))
        self.sidebar_recent_empty.set_visible(not recent)

    def _remove_recent_search(self, term: str) -> None:
        self.settings.remove_recent_search(term)
        self.toast(f"Removed “{term}” from recent searches")

    def _on_recent_searches_changed(self, _key, _value) -> None:
        self._update_sidebar_recent()
        if hasattr(self, "discover"):
            self.discover.refresh_recent_searches()

    def _on_sidebar_toggled(self, button) -> None:
        if self._syncing_sidebar:
            return
        self.settings.set("sidebar_visible", bool(button.get_active()))

    def _on_sidebar_setting_changed(self, _key, value) -> None:
        self._syncing_sidebar = True
        try:
            self._apply_sidebar_visibility(bool(value))
            self.sidebar_toggle.set_active(bool(value))
        finally:
            self._syncing_sidebar = False

    def _apply_sidebar_visibility(self, visible: bool) -> None:
        was_visible = self._sidebar_visible
        self._sidebar_visible = bool(visible)

        if hasattr(self, "sidebar_widget") and self.sidebar_widget.get_width() >= 200:
            self._last_sidebar_width = self.sidebar_widget.get_width()

        # Shrink the gallery before the sidebar takes its share of the window.
        # This keeps the window fixed and lets the justified rows reflow.
        if self._sidebar_visible and not was_visible:
            sidebar_width = max(220, self._last_sidebar_width)
            for grid in self._visible_grids():
                current = grid.get_width()
                if current > sidebar_width:
                    grid.prepare_for_width(current - sidebar_width)

        if hasattr(self, "sidebar_widget"):
            self.sidebar_widget.set_visible(self._sidebar_visible)
        if hasattr(self, "sidebar_toggle"):
            label = "Hide sidebar" if self._sidebar_visible else "Show sidebar"
            self.sidebar_toggle.set_tooltip_text(label)
            self.sidebar_toggle.update_property(
                [Gtk.AccessibleProperty.LABEL], [label]
            )
        GLib.timeout_add(80, self._relayout_galleries)

    def _visible_grids(self) -> list:
        grids = []
        if hasattr(self, "discover"):
            grids.append(self.discover.grid)
        if hasattr(self, "favorites_page"):
            grids.append(self.favorites_page.grid)
        return grids

    def _relayout_galleries(self):
        if hasattr(self, "discover"):
            self.discover.grid.request_relayout()
        if hasattr(self, "favorites_page"):
            self.favorites_page.grid.request_relayout()
        return False

    # -- page navigation -------------------------------------------------
    def switch_page(self, page_id: str, *, refresh: bool = True) -> None:
        if page_id not in ("discover", "favorites", "downloads"):
            return
        self._current_page = page_id
        self.page_stack.set_visible_child_name(page_id)
        row = self._nav_wrappers.get(page_id)
        if row is not None:
            self.nav_list.select_row(row)
            log.debug(
                "Selected nav page %s (row=%r, selected=%r)",
                page_id, row, self.nav_list.get_selected_row(),
            )
        if page_id == "discover":
            self._sync_source_selection(self.discover.current_source_id())
        else:
            self.source_list.select_row(None)
        if refresh and page_id == "favorites":
            self.favorites_page.refresh()
        elif refresh and page_id == "downloads":
            self.downloads_page.refresh()
        self._update_header()

    def _update_header(self) -> None:
        if self._current_page == "discover":
            source_id = self.discover.current_source_id() if hasattr(self, "discover") else "all"
            if source_id == "all":
                self.brand_subtitle.set_text("All sources · Discover")
            else:
                source = self.registry.get(source_id)
                self.brand_subtitle.set_text(
                    f"{source.name if source else source_id} · Discover"
                )
        elif self._current_page == "favorites":
            count = self.favorites.count()
            self.brand_subtitle.set_text(
                f"{count} favourite{'s' if count != 1 else ''}"
                if count
                else PAGE_TITLES["favorites"]
            )
        else:
            self.brand_subtitle.set_text(PAGE_TITLES["downloads"])

    def _determine_initial_source(self) -> str:
        enabled = set(self.settings.get("enabled_sources", []))
        source_id = self.settings.get("source", "danbooru")
        if source_id == "all" or source_id in enabled:
            return source_id
        return "all"

    def _initial_load(self):
        source_id = self._initial_source
        self.discover.set_source(source_id, run=False)
        self._sync_source_selection(source_id)
        self._update_header()
        self.discover.run_search(reset=True)
        return False

    def _on_source_selected(self, source_id: str, *, run: bool = True) -> None:
        self._changing_source = True
        try:
            self.settings.set("source", source_id)
        finally:
            self._changing_source = False
        self.discover.set_source(source_id, run=False)
        self._sync_source_selection(source_id)
        self.switch_page("discover", refresh=False)
        if run:
            self.discover.run_search(reset=True)

    def _search_tag(self, tag: str) -> None:
        source_id = self.discover.current_source_id()
        if source_id != "zerochan":
            tag = tag.replace(" ", "_")
        self.switch_page("discover", refresh=False)
        self.discover.search_tag(tag)

    # -- favourites / posts ---------------------------------------------
    def _toggle_favorite(self, post: Post) -> bool:
        state = self.favorites.toggle(post)
        self.discover.refresh_favorites()
        if self._current_page == "favorites" and not state:
            GLib.idle_add(self._refresh_favorites_idle)
        if state:
            self.toast(f"Added “{post.source_name}” artwork to favourites")
        else:
            self.toast("Removed from favourites")
        self._update_header()
        return state

    def _refresh_favorites_idle(self):
        self.favorites_page.refresh()
        return False

    def _open_post(self, post: Post) -> None:
        dialog = ImageDetailDialog(
            post,
            self.loader,
            self.favorites,
            on_download=self._download_post,
            on_favorite=self._toggle_favorite,
            on_tag_activate=self._search_tag,
        )
        self._dialogs.add(dialog)
        dialog.connect("closed", self._on_dialog_closed)
        dialog.present(self)

    def _on_dialog_closed(self, dialog) -> None:
        self._dialogs.discard(dialog)

    def _download_post(self, post: Post) -> None:
        if not post.file_url:
            self.toast("This source did not provide a direct download link.")
            return
        self.downloads.enqueue(post)
        toast = Adw.Toast.new(f"Downloading {post.source_name} artwork")
        toast.set_button_label("Downloads")
        toast.connect("button-clicked", lambda *_: self.switch_page("downloads"))
        self.toast_overlay.add_toast(toast)
        self._update_download_badge()

    def _on_download_event(self, _event: str, _task) -> None:
        self._update_download_badge()
        if self._current_page == "favorites":
            self._update_header()

    def _update_download_badge(self) -> None:
        count = self.downloads.active_count
        self._download_badge.set_text(str(count))
        self._download_badge.set_visible(count > 0)

    # -- safety ----------------------------------------------------------
    def request_safe_search(self, value: bool) -> bool:
        value = bool(value)
        current = bool(self.settings.get("safe_search", True))
        if value == current:
            self._sync_safe_controls(current)
            return True
        if value:
            self.settings.set("safe_search", True)
            self.toast("Safe search enabled")
            return True
        dialog = Adw.AlertDialog()
        dialog.set_heading("Show adult content?")
        dialog.set_body(
            "Safe search hides adult artwork at the source. Sources without a safe "
            "mode stay filtered. You can turn this back on at any time."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disable", "Show adult content")
        dialog.set_response_appearance("disable", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_confirm_nsfw)
        dialog.present(self)
        return False

    def _on_confirm_nsfw(self, _dialog, response: str) -> None:
        if response == "disable":
            self.settings.set("safe_search", False)
            self.toast("Safe search disabled — adult content may appear")
        else:
            self._sync_safe_controls(bool(self.settings.get("safe_search", True)))
        if self._preferences is not None:
            self._preferences.sync_safe()

    def _on_header_safe_toggled(self, button) -> None:
        if self._syncing_safe:
            return
        self.request_safe_search(bool(button.get_active()))

    def _on_sidebar_safe_toggled(self, switch, _param) -> None:
        if self._syncing_safe:
            return
        self.request_safe_search(bool(switch.get_active()))

    def _sync_safe_controls(self, safe: bool) -> None:
        self._syncing_safe = True
        try:
            self.header_safe.remove_css_class("on")
            self.header_safe.remove_css_class("off")
            self.header_safe.add_css_class("on" if safe else "off")
            self.header_safe.set_active(safe)
            self.safe_switch.set_active(safe)
            self.header_safe.set_tooltip_text(
                "Safe search is on" if safe else "Safe search is off"
            )
        finally:
            self._syncing_safe = False
        if hasattr(self, "discover"):
            self.discover.set_safe_state(safe)

    # -- settings reactions ----------------------------------------------
    def _on_safe_setting_changed(self, _key, value) -> None:
        self._sync_safe_controls(bool(value))
        if self._preferences is not None:
            self._preferences.sync_safe()

    def _on_blur_changed(self, _key, value) -> None:
        self.discover.set_blurred(bool(value))
        self.favorites_page.grid.set_blurred(bool(value))

    def _on_grid_changed(self, _key, value) -> None:
        self.discover.set_grid_size(value)
        self.favorites_page.grid.set_grid_size(value)

    def _on_accent_changed(self, _key, _value) -> None:
        self._apply_accent()

    def _on_scheme_changed(self, _key, _value) -> None:
        self._apply_color_scheme()

    def _on_sources_changed(self, _key, _value) -> None:
        self._rebuild_source_rows()
        self.discover.refresh_sources()
        self._sync_source_selection(self.discover.current_source_id())
        if self._current_page == "discover":
            self.discover.run_search(reset=True)
        self._update_header()

    def _on_dark_changed(self, *_args) -> None:
        self._apply_accent()

    def _apply_color_scheme(self) -> None:
        schemes = {
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }
        scheme = schemes.get(self.settings.get("color_scheme", "system"), Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def _apply_accent(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        accent = self.settings.get("accent", "mauve")
        if accent not in ACCENTS:
            accent = "mauve"
        self.theme.apply_accent(display, accent, Adw.StyleManager.get_default().get_dark())

    # -- app actions -----------------------------------------------------
    def show_preferences(self) -> None:
        if self._preferences is not None:
            self._preferences.present(self)
            return
        self._preferences = PreferencesDialog(
            self.settings,
            self.registry,
            on_request_safe=self.request_safe_search,
            on_notice=self.toast,
        )
        self._preferences.connect("closed", self._on_preferences_closed)
        self._preferences.present(self)

    def _on_preferences_closed(self, dialog) -> None:
        if self._preferences is dialog:
            self._preferences = None

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            application_version=__version__,
            developer_name="Hugh Lyell",
            developers=["Hugh Lyell"],
            copyright="© 2026 Hugh Lyell",
            license_type=Gtk.License.MIT_X11,
            website=APP_WEBSITE,
            issue_url=APP_ISSUES,
            comments=(
                "A fast, native anime image browser with modular sources, "
                "tag search and one-click downloads."
            ),
        )
        about.present(self)

    def focus_search(self) -> None:
        self.switch_page("discover", refresh=False)
        self.discover.focus_search()

    def refresh_page(self) -> None:
        if self._current_page == "discover":
            self.discover.run_search(reset=True)
        elif self._current_page == "favorites":
            self.favorites_page.refresh()
        else:
            self.downloads_page.refresh()

    def toggle_safe(self) -> None:
        self.request_safe_search(not bool(self.settings.get("safe_search", True)))

    # -- toasts ----------------------------------------------------------
    def toast(self, message: str, button_label: str | None = None, callback=None) -> None:
        toast = Adw.Toast.new(message)
        if button_label and callback is not None:
            toast.set_button_label(button_label)
            toast.connect("button-clicked", lambda *_: callback())
        self.toast_overlay.add_toast(toast)

    def _on_close_request(self, *_args) -> bool:
        if getattr(self.app, "window", None) is self:
            self.app.window = None
        for key, callback in self._settings_subscriptions:
            self.settings.unsubscribe(key, callback)
        self._settings_subscriptions.clear()
        self.downloads.remove_listener(self._on_download_event)
        try:
            self.downloads_page.shutdown()
        except Exception:
            pass
        try:
            Adw.StyleManager.get_default().disconnect(self._dark_handler)
        except Exception:
            pass
        if self._preferences is not None:
            try:
                self._preferences.close()
            except Exception:
                pass
            self._preferences = None
        for dialog in list(self._dialogs):
            try:
                dialog.close()
            except Exception:
                pass
        self._dialogs.clear()
        return False
