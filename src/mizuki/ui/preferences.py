"""Preferences dialog."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..sources import SourceRegistry  # noqa: E402
from .launch import launch_external  # noqa: E402
from .theme import ACCENT_LABELS  # noqa: E402

COLOR_SCHEMES = [("system", "Follow system"), ("light", "Light"), ("dark", "Dark")]
GRID_OPTIONS = [("compact", "Compact"), ("comfortable", "Comfortable"), ("large", "Large")]
SORT_OPTIONS = [("newest", "Newest"), ("score", "Top rated"), ("relevance", "Best match"), ("random", "Random")]


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(
        self,
        settings,
        registry: SourceRegistry,
        *,
        on_request_safe,
        on_notice,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.registry = registry
        self.on_request_safe = on_request_safe
        self.on_notice = on_notice
        self._syncing = False
        self.pages: dict[str, Adw.PreferencesPage] = {}

        self.pages["appearance"] = self._build_appearance_page()
        self.add(self.pages["appearance"])
        self.pages["content"] = self._build_content_page()
        self.add(self.pages["content"])
        self.pages["sources"] = self._build_sources_page()
        self.add(self.pages["sources"])
        self.pages["downloads"] = self._build_downloads_page()
        self.add(self.pages["downloads"])
        self.set_search_enabled(True)

    def show_page(self, name: str) -> None:
        page = self.pages.get(name)
        if page is not None:
            self.set_visible_page(page)

    # -- pages -----------------------------------------------------------
    def _build_appearance_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Appearance")
        page.set_icon_name("preferences-desktop-theme-symbolic")

        theme_group = Adw.PreferencesGroup()
        theme_group.set_title("Theme")
        theme_group.set_description("Mizuki follows Material You-inspired accent colours.")

        self.scheme_row = Adw.ComboRow()
        self.scheme_row.set_title("Colour scheme")
        self.scheme_row.set_model(Gtk.StringList.new([label for _, label in COLOR_SCHEMES]))
        scheme = self.settings.get("color_scheme", "system")
        indices = [key for key, _ in COLOR_SCHEMES]
        self.scheme_row.set_selected(indices.index(scheme) if scheme in indices else 0)
        self.scheme_row.connect("notify::selected", self._on_scheme_changed)
        theme_group.add(self.scheme_row)

        self.accent_row = Adw.ComboRow()
        self.accent_row.set_title("Accent colour")
        self._accent_ids = list(ACCENT_LABELS.keys())
        self.accent_row.set_model(
            Gtk.StringList.new([ACCENT_LABELS[accent] for accent in self._accent_ids])
        )
        accent = self.settings.get("accent", "mauve")
        self.accent_row.set_selected(
            self._accent_ids.index(accent) if accent in self._accent_ids else 0
        )
        self.accent_row.connect("notify::selected", self._on_accent_changed)
        theme_group.add(self.accent_row)
        page.add(theme_group)

        layout_group = Adw.PreferencesGroup()
        layout_group.set_title("Layout")
        self.grid_row = Adw.ComboRow()
        self.grid_row.set_title("Artwork size")
        self.grid_row.set_model(Gtk.StringList.new([label for _, label in GRID_OPTIONS]))
        grid = self.settings.get("grid_size", "comfortable")
        grid_ids = [key for key, _ in GRID_OPTIONS]
        self.grid_row.set_selected(grid_ids.index(grid) if grid in grid_ids else 1)
        self.grid_row.connect("notify::selected", self._on_grid_changed)
        layout_group.add(self.grid_row)
        page.add(layout_group)
        return page

    def _build_content_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Content")
        page.set_icon_name("security-high-symbolic")

        safety = Adw.PreferencesGroup()
        safety.set_title("Safety")
        safety.set_description(
            "Safe search filters adult results at the source. Sources without a safe "
            "subset always stay filtered."
        )
        self.safe_row = Adw.SwitchRow()
        self.safe_row.set_title("Safe search")
        self.safe_row.set_subtitle("Hide adult artwork from searches")
        self.safe_row.set_active(bool(self.settings.get("safe_search", True)))
        self.safe_row.connect("notify::active", self._on_safe_toggled)
        safety.add(self.safe_row)

        self.blur_row = Adw.SwitchRow()
        self.blur_row.set_title("Cover sensitive artwork")
        self.blur_row.set_subtitle("Hide sensitive thumbnails behind a warning")
        self.blur_row.set_active(bool(self.settings.get("blur_nsfw", True)))
        self.blur_row.connect("notify::active", self._on_blur_toggled)
        safety.add(self.blur_row)
        page.add(safety)

        defaults = Adw.PreferencesGroup()
        defaults.set_title("Search defaults")
        defaults.set_description("Used when Mizuki opens and when you switch to Discover.")

        self.default_source_row = Adw.ComboRow()
        self.default_source_row.set_title("Default source")
        self._default_source_ids: list[str] = []
        self._refresh_default_sources()
        self.default_source_row.connect("notify::selected", self._on_default_source_changed)
        defaults.add(self.default_source_row)

        self.default_sort_row = Adw.ComboRow()
        self.default_sort_row.set_title("Default sort")
        self.default_sort_row.set_model(Gtk.StringList.new([label for _, label in SORT_OPTIONS]))
        sort = self.settings.get("sort", "newest")
        sort_ids = [key for key, _ in SORT_OPTIONS]
        self.default_sort_row.set_selected(sort_ids.index(sort) if sort in sort_ids else 0)
        self.default_sort_row.connect("notify::selected", self._on_sort_changed)
        defaults.add(self.default_sort_row)
        page.add(defaults)
        return page

    def _build_sources_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Sources")
        page.set_icon_name("network-workgroup-symbolic")

        group = Adw.PreferencesGroup()
        group.set_title("Enabled sources")
        group.set_description(
            "At least one source must stay enabled. Disabled sources disappear from "
            "the sidebar and from “All sources” results."
        )
        self.source_rows: dict[str, Adw.SwitchRow] = {}
        enabled = set(self.settings.get("enabled_sources", []))
        for source in self.registry.all():
            row = Adw.SwitchRow()
            row.set_title(source.name)
            subtitle = source.description
            if not source.supports_nsfw:
                subtitle += " · SFW only"
            row.set_subtitle(subtitle)
            row.set_active(source.id in enabled)
            row.connect("notify::active", self._on_source_toggled, source.id)
            group.add(row)
            self.source_rows[source.id] = row
        page.add(group)

        plugins = Adw.PreferencesGroup()
        plugins.set_title("Custom sources")
        plugins.set_description(
            "Drop a Python file into this folder to add your own booru adapter. "
            "Expose a Source subclass or instance as SOURCE."
        )
        row = Adw.ActionRow()
        row.set_title("Plugin folder")
        row.set_subtitle(str(self.registry.plugin_directory))
        row.set_subtitle_lines(2)
        open_button = Gtk.Button(label="Open")
        open_button.add_css_class("flat")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.connect("clicked", self._open_plugin_folder)
        row.add_suffix(open_button)
        plugins.add(row)

        docs = Adw.ActionRow()
        docs.set_title("Plugin API")
        docs.set_subtitle("mizuki.sources.Source · see docs/SOURCES.md")
        docs.set_subtitle_lines(2)
        docs_open = Gtk.Button(label="Open")
        docs_open.add_css_class("flat")
        docs_open.set_valign(Gtk.Align.CENTER)
        docs_open.connect("clicked", self._open_docs)
        docs.add_suffix(docs_open)
        plugins.add(docs)
        page.add(plugins)
        return page

    def _build_downloads_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage()
        page.set_title("Downloads")
        page.set_icon_name("folder-download-symbolic")

        group = Adw.PreferencesGroup()
        group.set_title("Storage")
        self.folder_row = Adw.ActionRow()
        self.folder_row.set_title("Download folder")
        self.folder_row.set_subtitle(str(self.settings.get("download_dir", "")))
        self.folder_row.set_subtitle_lines(2)
        choose = Gtk.Button(label="Choose…")
        choose.add_css_class("flat")
        choose.set_valign(Gtk.Align.CENTER)
        choose.connect("clicked", self._choose_folder)
        self.folder_row.add_suffix(choose)
        group.add(self.folder_row)

        open_row = Adw.ActionRow()
        open_row.set_title("Open download folder")
        open_open = Gtk.Button(label="Open")
        open_open.add_css_class("flat")
        open_open.set_valign(Gtk.Align.CENTER)
        open_open.connect("clicked", self._open_download_folder)
        open_row.add_suffix(open_open)
        group.add(open_row)
        page.add(group)

        note = Adw.PreferencesGroup()
        note.set_title("File names")
        note.set_description(
            "Files are named with the source, post id and artist so they stay easy to "
            "find. Existing files are never overwritten."
        )
        page.add(note)
        return page

    # -- appearance ------------------------------------------------------
    def _on_scheme_changed(self, row, _param) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        if 0 <= index < len(COLOR_SCHEMES):
            self.settings.set("color_scheme", COLOR_SCHEMES[index][0])

    def _on_accent_changed(self, row, _param) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        if 0 <= index < len(self._accent_ids):
            self.settings.set("accent", self._accent_ids[index])

    def _on_grid_changed(self, row, _param) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        if 0 <= index < len(GRID_OPTIONS):
            self.settings.set("grid_size", GRID_OPTIONS[index][0])

    # -- content ---------------------------------------------------------
    def _on_safe_toggled(self, row, _param) -> None:
        if self._syncing:
            return
        requested = bool(row.get_active())
        if requested == self.settings.get("safe_search", True):
            return
        accepted = bool(self.on_request_safe(requested))
        if not accepted:
            self._syncing = True
            row.set_active(bool(self.settings.get("safe_search", True)))
            self._syncing = False

    def _on_blur_toggled(self, row, _param) -> None:
        if self._syncing:
            return
        self.settings.set("blur_nsfw", bool(row.get_active()))

    def _refresh_default_sources(self) -> None:
        enabled = set(self.settings.get("enabled_sources", []))
        sources = [source for source in self.registry.all() if source.id in enabled]
        self._default_source_ids = [source.id for source in sources]
        self._syncing = True
        self.default_source_row.set_model(
            Gtk.StringList.new([source.name for source in sources])
        )
        current = self.settings.get("source", "danbooru")
        if current in self._default_source_ids:
            self.default_source_row.set_selected(self._default_source_ids.index(current))
        elif self._default_source_ids:
            self.default_source_row.set_selected(0)
        self._syncing = False

    def _on_default_source_changed(self, row, _param) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        if 0 <= index < len(self._default_source_ids):
            self.settings.set("source", self._default_source_ids[index])

    def _on_sort_changed(self, row, _param) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        if 0 <= index < len(SORT_OPTIONS):
            self.settings.set("sort", SORT_OPTIONS[index][0])

    # -- sources ---------------------------------------------------------
    def _on_source_toggled(self, row, _param, source_id: str) -> None:
        if self._syncing:
            return
        enabled = list(self.settings.get("enabled_sources", []))
        if row.get_active():
            if source_id not in enabled:
                enabled.append(source_id)
        else:
            if len(enabled) <= 1:
                self._syncing = True
                row.set_active(True)
                self._syncing = False
                self.on_notice("Keep at least one source enabled.")
                return
            enabled = [item for item in enabled if item != source_id]
        self.settings.set("enabled_sources", enabled)
        self._refresh_default_sources()

    def sync_safe(self) -> None:
        self._syncing = True
        self.safe_row.set_active(bool(self.settings.get("safe_search", True)))
        self._syncing = False

    def _open_plugin_folder(self, _button) -> None:
        directory = self.registry.plugin_directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.on_notice(f"Could not open folder: {exc}")
            return
        launch_external(directory.as_uri(), parent=self, on_error=self.on_notice)

    def _open_docs(self, _row) -> None:
        launch_external(
            "https://github.com/HughLyell/mizuki/blob/main/docs/SOURCES.md",
            parent=self,
            on_error=self.on_notice,
        )

    # -- downloads -------------------------------------------------------
    def _choose_folder(self, _button) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a download folder")
        current = Path(str(self.settings.get("download_dir", ""))).expanduser()
        try:
            if current.exists():
                dialog.set_initial_folder(Gio.File.new_for_path(str(current)))
        except Exception:
            pass
        dialog.select_folder(None, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None:
            return
        path = folder.get_path()
        if not path:
            return
        self.settings.set("download_dir", path)
        self.folder_row.set_subtitle(path)
        self.on_notice("Download folder updated.")

    def _open_download_folder(self, _row) -> None:
        directory = Path(str(self.settings.get("download_dir", ""))).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.on_notice(f"Could not open folder: {exc}")
            return
        launch_external(directory.as_uri(), parent=self, on_error=self.on_notice)
