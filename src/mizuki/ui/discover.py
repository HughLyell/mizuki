"""Discover/search page."""

from __future__ import annotations

import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from ..models import Post, SearchQuery, parse_tags  # noqa: E402
from ..services.search import SearchController  # noqa: E402
from ..sources import SourceRegistry  # noqa: E402
from .grid import PostGrid  # noqa: E402

log = logging.getLogger(__name__)

SORT_OPTIONS = [
    ("newest", "Newest"),
    ("score", "Top rated"),
    ("relevance", "Best match"),
    ("random", "Random"),
]


class DiscoverPage(Gtk.Box):
    def __init__(
        self,
        settings,
        registry: SourceRegistry,
        loader,
        favorites,
        search_controller: SearchController,
        *,
        on_open_post,
        on_download,
        on_favorite,
        on_source_changed,
        on_request_safe,
        on_warning,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.settings = settings
        self.registry = registry
        self.loader = loader
        self.favorites = favorites
        self.search_controller = search_controller
        self.on_open_post = on_open_post
        self.on_download = on_download
        self.on_favorite = on_favorite
        self.on_source_changed = on_source_changed
        self.on_request_safe = on_request_safe
        self.on_warning = on_warning

        self._source_id = "all"
        self._syncing = False
        self._loading = False
        self._has_more = False
        self._page = 1
        self._query = SearchQuery(safe=settings.get("safe_search", True))
        self._suggest_generation = 0
        self._suggest_timeout_id = 0

        self.set_hexpand(True)
        self.set_vexpand(True)

        self.append(self._build_search_panel())
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(160)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        self.grid = PostGrid(
            loader,
            settings,
            favorites,
            on_activate=on_open_post,
            on_download=on_download,
            on_favorite=on_favorite,
            on_load_more=self.load_more,
        )
        self._stack.add_named(self.grid, "grid")
        self._stack.add_named(self._build_loading_state(), "loading")
        self._stack.add_named(self._build_empty_state(), "empty")
        self._stack.add_named(self._build_error_state(), "error")
        self._stack.set_visible_child_name("loading")

        self._refresh_source_model()
        self._sync_safe_button(settings.get("safe_search", True))
        self._sync_sort()
        self._build_query_chips()
        self._update_empty_suggestions()
        self._suggest_popover = self._build_suggest_popover()

    # -- construction ----------------------------------------------------
    def _build_search_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.set_margin_top(16)
        panel.set_margin_bottom(10)
        panel.set_margin_start(24)
        panel.set_margin_end(24)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.add_css_class("search-entry")
        self.search_entry.set_placeholder_text("Search tags, characters, artists…")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search_activated)
        self.search_entry.connect("search-changed", self._on_search_changed)
        search_row.append(self.search_entry)

        self.search_button = Gtk.Button()
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        button_box.set_halign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("system-search-symbolic")
        icon.set_pixel_size(16)
        button_box.append(icon)
        button_box.append(Gtk.Label(label="Search"))
        self.search_button.set_child(button_box)
        self.search_button.add_css_class("suggested-action")
        self.search_button.add_css_class("search-button")
        self.search_button.connect("clicked", self._on_search_activated)
        search_row.append(self.search_button)
        panel.append(search_row)

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.source_drop = Gtk.DropDown()
        self.source_drop.add_css_class("filter-drop")
        self.source_drop.set_tooltip_text("Image source")
        self.source_drop.set_size_request(168, -1)
        self.source_drop.connect("notify::selected", self._on_source_drop_changed)
        filter_row.append(self.source_drop)

        self.safe_button = Gtk.ToggleButton()
        self.safe_button.add_css_class("sfw-pill")
        self.safe_button.set_tooltip_text("Toggle safe search")
        safe_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        safe_box.set_halign(Gtk.Align.CENTER)
        shield = Gtk.Image.new_from_icon_name("security-high-symbolic")
        shield.set_pixel_size(14)
        safe_box.append(shield)
        safe_box.append(Gtk.Label(label="SFW"))
        self.safe_button.set_child(safe_box)
        self.safe_button.connect("toggled", self._on_safe_toggled)
        filter_row.append(self.safe_button)

        self.sort_drop = Gtk.DropDown.new_from_strings([label for _, label in SORT_OPTIONS])
        self.sort_drop.add_css_class("filter-drop")
        self.sort_drop.set_tooltip_text("Sort order")
        self.sort_drop.set_size_request(148, -1)
        self.sort_drop.connect("notify::selected", self._on_sort_changed)
        filter_row.append(self.sort_drop)

        self.count_label = Gtk.Label(label="")
        self.count_label.add_css_class("count-label")
        self.count_label.set_hexpand(True)
        self.count_label.set_halign(Gtk.Align.END)
        self.count_label.set_valign(Gtk.Align.CENTER)
        filter_row.append(self.count_label)
        panel.append(filter_row)

        self.query_chips = Gtk.FlowBox()
        self.query_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self.query_chips.set_min_children_per_line(1)
        self.query_chips.set_max_children_per_line(10)
        self.query_chips.set_column_spacing(6)
        self.query_chips.set_row_spacing(6)
        self.query_chips.set_valign(Gtk.Align.START)
        self.query_chips.set_visible(False)
        panel.append(self.query_chips)
        return panel

    def _build_loading_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        spinner = Gtk.Spinner()
        spinner.set_size_request(34, 34)
        spinner.start()
        label = Gtk.Label(label="Finding beautiful artwork…")
        label.add_css_class("dim-label")
        box.append(spinner)
        box.append(label)
        return box

    def _build_empty_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_start(24)
        box.set_margin_end(24)
        icon = Gtk.Image.new_from_icon_name("system-search-symbolic")
        icon.set_pixel_size(52)
        icon.add_css_class("dim-label")
        box.append(icon)
        title = Gtk.Label(label="No artwork found")
        title.add_css_class("big-title")
        box.append(title)
        self.empty_description = Gtk.Label(
            label="Try fewer tags, a different spelling, or browse another source."
        )
        self.empty_description.add_css_class("dim-label")
        self.empty_description.set_justify(Gtk.Justification.CENTER)
        self.empty_description.set_wrap(True)
        box.append(self.empty_description)

        self.recent_title = Gtk.Label(label="RECENT SEARCHES")
        self.recent_title.add_css_class("detail-section-title")
        self.recent_title.set_margin_top(6)
        self.recent_title.set_visible(False)
        box.append(self.recent_title)
        self.recent_chips = Gtk.FlowBox()
        self.recent_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_chips.set_min_children_per_line(1)
        self.recent_chips.set_max_children_per_line(6)
        self.recent_chips.set_row_spacing(6)
        self.recent_chips.set_column_spacing(6)
        self.recent_chips.set_halign(Gtk.Align.CENTER)
        self.recent_chips.set_visible(False)
        box.append(self.recent_chips)

        popular_title = Gtk.Label(label="POPULAR TAGS")
        popular_title.add_css_class("detail-section-title")
        popular_title.set_margin_top(6)
        box.append(popular_title)
        self.empty_chips = Gtk.FlowBox()
        self.empty_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        self.empty_chips.set_min_children_per_line(1)
        self.empty_chips.set_max_children_per_line(6)
        self.empty_chips.set_row_spacing(6)
        self.empty_chips.set_column_spacing(6)
        self.empty_chips.set_halign(Gtk.Align.CENTER)
        self.empty_chips.set_margin_top(8)
        box.append(self.empty_chips)
        return box

    def _build_error_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_start(24)
        box.set_margin_end(24)
        icon = Gtk.Image.new_from_icon_name("network-error-symbolic")
        icon.set_pixel_size(52)
        icon.add_css_class("dim-label")
        box.append(icon)
        title = Gtk.Label(label="Couldn't load results")
        title.add_css_class("big-title")
        box.append(title)
        self.error_label = Gtk.Label(label="")
        self.error_label.add_css_class("dim-label")
        self.error_label.set_wrap(True)
        self.error_label.set_justify(Gtk.Justification.CENTER)
        self.error_label.set_max_width_chars(64)
        self.error_label.set_selectable(True)
        box.append(self.error_label)
        retry = Gtk.Button(label="Try again")
        retry.add_css_class("pill-button")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self.run_search(reset=True))
        box.append(retry)
        return box

    def _build_suggest_popover(self) -> Gtk.Popover:
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_has_arrow(False)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.set_parent(self.search_entry)
        popover.set_size_request(360, -1)
        self.suggest_list = Gtk.ListBox()
        self.suggest_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.suggest_list.add_css_class("navigation-sidebar")
        self.suggest_list.connect("row-activated", self._on_suggestion_activated)
        popover.set_child(self.suggest_list)
        return popover

    # -- public API ------------------------------------------------------
    def current_source_id(self) -> str:
        return self._source_id

    def set_source(self, source_id: str, *, run: bool = False) -> None:
        self._source_id = source_id
        self._sync_source_drop()
        self._update_empty_suggestions()
        if run:
            self.run_search(reset=True)

    def refresh_sources(self) -> None:
        enabled = set(self.settings.get("enabled_sources", []))
        if self._source_id != "all" and self._source_id not in enabled:
            self._source_id = "all"
        self._refresh_source_model()
        self._update_empty_suggestions()

    def focus_search(self) -> None:
        self.search_entry.grab_focus()

    def set_safe_state(self, safe: bool) -> None:
        self._sync_safe_button(safe)
        self.grid.set_blurred(bool(self.settings.get("blur_nsfw", True)))
        if self._query.safe != safe:
            self.run_search(reset=True)

    def set_grid_size(self, size: str) -> None:
        self.grid.set_grid_size(size)

    def set_blurred(self, blurred: bool) -> None:
        self.grid.set_blurred(blurred)

    def refresh_favorites(self) -> None:
        self.grid.refresh_favorites()

    def search_tag(self, tag: str) -> None:
        tag = tag.strip()
        if not tag:
            return
        self.search_entry.set_text(tag)
        self.run_search(reset=True, record=True)

    def run_search(self, *, reset: bool = True, record: bool = False) -> None:
        text = self.search_entry.get_text().strip()
        safe = bool(self.settings.get("safe_search", True))
        sort = self._current_sort()
        limit = 24 if self._source_id == "all" else 40
        if reset:
            self._page = 1
            self._query = SearchQuery(text=text, page=1, limit=limit, safe=safe, sort=sort)
            self._has_more = False
            self.grid.clear()
        else:
            if self._loading or not self._has_more:
                return
            self._page += 1
            self._query = self._query.with_page(self._page)
        if record and text:
            self.settings.add_recent_search(text)
            self._update_empty_suggestions()
        sources = self._selected_sources()
        self._build_query_chips()
        self._loading = True
        self.search_button.set_sensitive(False)
        self.count_label.set_text("Searching…")
        if reset and self.grid.count == 0:
            self._stack.set_visible_child_name("loading")
            self.grid.set_state(loading=False, has_more=False)
        else:
            self._stack.set_visible_child_name("grid")
            self.grid.set_state(loading=True)
        self._suggest_generation += 1

        self.search_controller.search(
            sources,
            self._query,
            on_start=None,
            on_results=self._on_results,
            on_error=self._on_error,
            on_finished=self._on_finished,
        )

    def load_more(self) -> None:
        if self._loading or not self._has_more:
            return
        self.run_search(reset=False)

    # -- search results --------------------------------------------------
    def _on_results(self, posts: list[Post], has_more: bool, errors: list[str]) -> None:
        if not self._loading:
            return
        self._loading = False
        before = self.grid.count
        self._has_more = has_more
        if posts:
            self.grid.append(posts)
        self.grid.set_state(loading=False, has_more=has_more)
        if self._query.page > 1 and self.grid.count == before:
            # A page made entirely of duplicates; stop rather than hammering
            # the source while the user is at the bottom of the grid.
            self._has_more = False
            self.grid.set_state(has_more=False)
        count = self.grid.count
        self.count_label.set_text(f"{count} artwork{'s' if count != 1 else ''}")
        if self.grid.count > 0:
            self._stack.set_visible_child_name("grid")
        elif errors:
            self._show_error(errors[0])
        else:
            self._stack.set_visible_child_name("empty")
        if errors:
            self.on_warning(errors[0] if len(errors) == 1 else f"{len(errors)} sources could not be reached.")
        if not posts and not errors and self._has_more:
            self._has_more = False
            self.grid.set_state(has_more=False)

    def _on_error(self, message: str) -> None:
        self._loading = False
        self.grid.set_state(loading=False, has_more=False)
        if self.grid.count == 0:
            self._show_error(message)
        else:
            self.on_warning(message)

    def _on_finished(self) -> None:
        self._loading = False
        self.search_button.set_sensitive(True)
        self.grid.set_state(loading=False)

    def _show_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self._stack.set_visible_child_name("error")
        self.count_label.set_text("")

    # -- search box callbacks --------------------------------------------
    def _on_search_activated(self, *_args) -> None:
        self._suggest_popover.popdown()
        self.run_search(reset=True, record=True)

    def _on_search_changed(self, _entry) -> None:
        if self._suggest_timeout_id:
            GLib.source_remove(self._suggest_timeout_id)
        self._suggest_timeout_id = GLib.timeout_add(320, self._schedule_suggestions)

    def _schedule_suggestions(self):
        self._suggest_timeout_id = 0
        text = self.search_entry.get_text()
        tags = parse_tags(text)
        if not tags or len(tags[-1]) < 2:
            self._suggest_popover.popdown()
            return False
        source = self._suggestion_source()
        if source is None:
            return False
        prefix = tags[-1].lstrip("-")
        self._suggest_generation += 1
        generation = self._suggest_generation

        def fetch():
            try:
                names = source.suggest_tags(prefix, limit=8)
            except Exception:
                names = []
            GLib.idle_add(self._deliver_suggestions, generation, names)

        threading.Thread(target=fetch, daemon=True, name="mizuki-suggest").start()
        return False

    def _deliver_suggestions(self, generation: int, names: list[str]):
        if generation != self._suggest_generation:
            return False
        if not self.search_entry.has_focus():
            return False
        child = self.suggest_list.get_first_child()
        while child is not None:
            self.suggest_list.remove(child)
            child = self.suggest_list.get_first_child()
        if not names:
            self._suggest_popover.popdown()
            return False
        for name in names[:8]:
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=name)
            label.set_xalign(0)
            label.set_margin_top(7)
            label.set_margin_bottom(7)
            label.set_margin_start(12)
            label.set_margin_end(12)
            row.set_child(label)
            row._mizuki_tag = name  # type: ignore[attr-defined]
            self.suggest_list.append(row)
        try:
            self._suggest_popover.popup()
        except Exception:
            pass
        return False

    def _on_suggestion_activated(self, _list, row) -> None:
        name = getattr(row, "_mizuki_tag", None)
        if not name:
            return
        tags = parse_tags(self.search_entry.get_text())
        if tags:
            tags[-1] = name
        else:
            tags = [name]
        self.search_entry.set_text(" ".join(tags) + " ")
        self.search_entry.set_position(-1)
        self.search_entry.grab_focus()
        self._suggest_popover.popdown()

    def _suggestion_source(self):
        if self._source_id != "all":
            return self.registry.get(self._source_id)
        for source in self._selected_sources():
            if source.id in ("danbooru", "safebooru"):
                return source
        return None

    # -- filter callbacks ------------------------------------------------
    def _on_source_drop_changed(self, dropdown, _param) -> None:
        if self._syncing:
            return
        index = dropdown.get_selected()
        if index < 0 or index >= len(self._source_ids):
            return
        source_id = self._source_ids[index]
        self._source_id = source_id
        self._update_empty_suggestions()
        self.on_source_changed(source_id)
        self.run_search(reset=True)

    def _on_sort_changed(self, dropdown, _param) -> None:
        if self._syncing:
            return
        index = dropdown.get_selected()
        if index < 0 or index >= len(SORT_OPTIONS):
            return
        self.settings.set("sort", SORT_OPTIONS[index][0])
        if self._stack.get_visible_child_name() in ("grid", "empty", "error"):
            self.run_search(reset=True)

    def _on_safe_toggled(self, button) -> None:
        if self._syncing:
            return
        self.on_request_safe(button.get_active())

    def _sync_safe_button(self, safe: bool) -> None:
        self._syncing = True
        self.safe_button.set_active(safe)
        self.safe_button.remove_css_class("on")
        self.safe_button.remove_css_class("off")
        self.safe_button.add_css_class("on" if safe else "off")
        self.safe_button.set_tooltip_text(
            "Safe search is on — click to show adult content"
            if safe
            else "Safe search is off — adult content may appear"
        )
        self._syncing = False

    def _sync_sort(self) -> None:
        self._syncing = True
        sort = self.settings.get("sort", "newest")
        ids = [option_id for option_id, _ in SORT_OPTIONS]
        self.sort_drop.set_selected(ids.index(sort) if sort in ids else 0)
        self._syncing = False

    def _current_sort(self) -> str:
        index = self.sort_drop.get_selected()
        if 0 <= index < len(SORT_OPTIONS):
            return SORT_OPTIONS[index][0]
        return "newest"

    def sync_sort(self, *, run: bool = False) -> None:
        current = self._current_sort()
        self._sync_sort()
        new = self._current_sort()
        if run and new != current:
            self.run_search(reset=True)

    def _sync_source_drop(self) -> None:
        self._syncing = True
        try:
            if self._source_id in self._source_ids:
                self.source_drop.set_selected(self._source_ids.index(self._source_id))
            else:
                self._source_id = "all"
                self.source_drop.set_selected(0)
        finally:
            self._syncing = False

    # -- source model ----------------------------------------------------
    def _refresh_source_model(self) -> None:
        enabled = set(self.settings.get("enabled_sources", []))
        self._source_ids = ["all"] + [
            source.id for source in self.registry.all() if source.id in enabled
        ]
        labels = ["All sources"] + [
            source.name for source in self.registry.all() if source.id in enabled
        ]
        self._syncing = True
        self.source_drop.set_model(Gtk.StringList.new(labels))
        if self._source_id not in self._source_ids:
            self._source_id = "all"
        self.source_drop.set_selected(self._source_ids.index(self._source_id))
        self._syncing = False

    def _selected_sources(self):
        if self._source_id == "all":
            enabled = self.settings.get("enabled_sources", [])
            return self.registry.select(enabled) or self.registry.all()
        source = self.registry.get(self._source_id)
        return [source] if source is not None else []

    # -- query chips -----------------------------------------------------
    def _build_query_chips(self) -> None:
        child = self.query_chips.get_first_child()
        while child is not None:
            self.query_chips.remove(child)
            child = self.query_chips.get_first_child()
        tags = self._query.tags
        for tag in tags:
            button = Gtk.Button()
            button.add_css_class("query-chip")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            box.append(Gtk.Label(label=tag))
            close = Gtk.Image.new_from_icon_name("window-close-symbolic")
            close.set_pixel_size(10)
            box.append(close)
            button.set_child(box)
            button.set_tooltip_text(f"Remove “{tag}”")
            button.connect("clicked", self._remove_tag, tag)
            self.query_chips.append(button)
        self.query_chips.set_visible(bool(tags))

    def _remove_tag(self, _button, tag: str) -> None:
        tags = [item for item in parse_tags(self.search_entry.get_text()) if item != tag]
        self.search_entry.set_text(" ".join(tags))
        self.run_search(reset=True)

    # -- empty state suggestions -----------------------------------------
    def _update_empty_suggestions(self) -> None:
        for flow in (self.empty_chips, self.recent_chips):
            child = flow.get_first_child()
            while child is not None:
                flow.remove(child)
                child = flow.get_first_child()

        recent = [item for item in self.settings.get("recent_searches", []) if item][:6]
        for term in recent:
            chip = Gtk.Button(label=term)
            chip.add_css_class("query-chip")
            chip.connect("clicked", lambda _b, value=term: self.search_tag(value))
            self.recent_chips.append(chip)
        self.recent_chips.set_visible(bool(recent))
        self.recent_title.set_visible(bool(recent))

        source = self.registry.get(self._source_id) if self._source_id != "all" else None
        tags = list(source.popular_tags) if source and source.popular_tags else [
            "landscape",
            "scenery",
            "1girl",
            "sky",
            "city",
            "flowers",
        ]
        for tag in tags[:8]:
            chip = Gtk.Button(label=tag)
            chip.add_css_class("tag-pill")
            chip.connect("clicked", lambda _b, value=tag: self.search_tag(value))
            self.empty_chips.append(chip)
