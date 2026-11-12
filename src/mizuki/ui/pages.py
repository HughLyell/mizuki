"""Favourites and downloads pages."""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from ..models import Post  # noqa: E402
from .grid import PostGrid  # noqa: E402

log = logging.getLogger(__name__)


class FavoritesPage(Gtk.Box):
    def __init__(
        self,
        loader,
        settings,
        favorites,
        *,
        on_activate,
        on_download,
        on_favorite,
        on_discover,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.favorites = favorites
        self.on_favorite_external = on_favorite
        self.set_hexpand(True)
        self.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_margin_top(22)
        header.set_margin_bottom(8)
        header.set_margin_start(26)
        header.set_margin_end(26)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Favourites")
        title.add_css_class("page-heading")
        title.set_xalign(0)
        self.subtitle = Gtk.Label(label="Artwork you have saved")
        self.subtitle.add_css_class("page-subheading")
        self.subtitle.set_xalign(0)
        titles.append(title)
        titles.append(self.subtitle)
        header.append(titles)
        self.append(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.append(self.stack)

        self.grid = PostGrid(
            loader,
            settings,
            favorites,
            on_activate=on_activate,
            on_download=on_download,
            on_favorite=self._on_favorite,
            show_footer=False,
        )
        self.stack.add_named(self.grid, "grid")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_vexpand(True)
        icon = Gtk.Image.new_from_icon_name("non-starred-symbolic")
        icon.set_pixel_size(52)
        icon.add_css_class("dim-label")
        empty.append(icon)
        empty_title = Gtk.Label(label="No favourites yet")
        empty_title.add_css_class("big-title")
        empty.append(empty_title)
        description = Gtk.Label(
            label="Tap the star on any artwork to keep it here."
        )
        description.add_css_class("dim-label")
        description.set_justify(Gtk.Justification.CENTER)
        description.set_wrap(True)
        empty.append(description)
        discover = Gtk.Button(label="Discover artwork")
        discover.add_css_class("pill-button")
        discover.add_css_class("suggested-action")
        discover.set_halign(Gtk.Align.CENTER)
        discover.set_margin_top(6)
        discover.connect("clicked", lambda *_: on_discover())
        empty.append(discover)
        self.stack.add_named(empty, "empty")

        self.refresh()

    def refresh(self) -> None:
        posts = self.favorites.all()
        self.grid.clear()
        self.grid.append(posts)
        count = len(posts)
        self.subtitle.set_text(
            f"{count} saved artwork{'s' if count != 1 else ''}" if count else "Artwork you have saved"
        )
        self.stack.set_visible_child_name("grid" if posts else "empty")

    def _on_favorite(self, post: Post) -> bool:
        state = bool(self.on_favorite_external(post))
        if not state:
            GLib.idle_add(self._refresh_idle)
        return state

    def _refresh_idle(self):
        self.refresh()
        return False


class DownloadsPage(Gtk.Box):
    def __init__(self, settings, manager, *, on_discover, on_notice) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.settings = settings
        self.manager = manager
        self.on_discover = on_discover
        self.on_notice = on_notice
        self._rows: dict[str, tuple[Gtk.ListBoxRow, Gtk.Label, Gtk.ProgressBar, Gtk.Button]] = {}
        self.set_hexpand(True)
        self.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(22)
        header.set_margin_bottom(8)
        header.set_margin_start(26)
        header.set_margin_end(26)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Downloads")
        title.add_css_class("page-heading")
        title.set_xalign(0)
        self.subtitle = Gtk.Label(label="Your saved artwork")
        self.subtitle.add_css_class("page-subheading")
        self.subtitle.set_xalign(0)
        titles.append(title)
        titles.append(self.subtitle)
        titles.set_hexpand(True)
        header.append(titles)

        open_folder = Gtk.Button(icon_name="folder-open-symbolic")
        open_folder.add_css_class("pill-button")
        open_folder.set_tooltip_text("Open the downloads folder")
        open_folder.connect("clicked", self._open_folder)
        header.append(open_folder)

        self.clear_button = Gtk.Button(label="Clear finished")
        self.clear_button.add_css_class("pill-button")
        self.clear_button.connect("clicked", self._clear_finished)
        header.append(self.clear_button)
        self.append(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_vexpand(True)
        self.append(self.stack)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_margin_top(6)
        self.listbox.set_margin_bottom(24)
        self.listbox.set_margin_start(26)
        self.listbox.set_margin_end(26)
        scroll.set_child(self.listbox)
        self.stack.add_named(scroll, "list")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_vexpand(True)
        icon = Gtk.Image.new_from_icon_name("folder-download-symbolic")
        icon.set_pixel_size(52)
        icon.add_css_class("dim-label")
        empty.append(icon)
        empty_title = Gtk.Label(label="No downloads yet")
        empty_title.add_css_class("big-title")
        empty.append(empty_title)
        description = Gtk.Label(label="Download artwork from any post and it will appear here.")
        description.add_css_class("dim-label")
        description.set_wrap(True)
        description.set_justify(Gtk.Justification.CENTER)
        empty.append(description)
        discover = Gtk.Button(label="Browse artwork")
        discover.add_css_class("pill-button")
        discover.add_css_class("suggested-action")
        discover.set_halign(Gtk.Align.CENTER)
        discover.set_margin_top(6)
        discover.connect("clicked", lambda *_: self.on_discover())
        empty.append(discover)
        self.stack.add_named(empty, "empty")

        self.manager.add_listener(self._on_manager_event)
        self.refresh()

    # -- public ----------------------------------------------------------
    def shutdown(self) -> None:
        self.manager.remove_listener(self._on_manager_event)

    def refresh(self) -> None:
        for task_id in list(self._rows):
            row = self._rows[task_id][0]
            parent = row.get_parent()
            if isinstance(parent, Gtk.ListBox):
                parent.remove(row)
            del self._rows[task_id]
        for task in self.manager.tasks:
            self._add_row(task)
        self._update_state()

    # -- manager events --------------------------------------------------
    def _on_manager_event(self, event: str, task) -> None:
        if event == "added":
            self._add_row(task)
            self._update_state()
        elif event == "updated" and task.id in self._rows:
            self._update_row(task)
            self._update_state()
        elif event == "removed":
            entry = self._rows.pop(task.id, None)
            if entry is not None:
                parent = entry[0].get_parent()
                if isinstance(parent, Gtk.ListBox):
                    parent.remove(entry[0])
            self._update_state()

    def _add_row(self, task) -> None:
        if task.id in self._rows:
            self._update_row(task)
            return
        row = Gtk.ListBoxRow()
        row.add_css_class("download-row")
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)

        icon = Gtk.Image.new_from_icon_name(self._status_icon(task.status))
        icon.set_pixel_size(20)
        icon.set_valign(Gtk.Align.CENTER)
        box.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.CENTER)
        text.set_hexpand(True)
        filename = Gtk.Label(label=task.filename)
        filename.set_xalign(0)
        filename.set_ellipsize(Pango.EllipsizeMode.END)
        filename.set_tooltip_text(str(task.path))
        status = Gtk.Label(label="")
        status.add_css_class("dim-label")
        status.set_xalign(0)
        status.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(filename)
        text.append(status)
        box.append(text)

        progress = Gtk.ProgressBar()
        progress.add_css_class("download-progress")
        progress.set_size_request(150, -1)
        progress.set_valign(Gtk.Align.CENTER)
        box.append(progress)

        action = Gtk.Button()
        action.add_css_class("flat")
        action.set_valign(Gtk.Align.CENTER)
        action.connect("clicked", self._on_action_clicked, task)
        box.append(action)

        row.set_child(box)
        self._rows[task.id] = (row, status, progress, action)
        self.listbox.insert(row, 0)
        self._update_row(task)

    def _update_row(self, task) -> None:
        entry = self._rows.get(task.id)
        if entry is None:
            return
        row, status, progress, action = entry
        status.set_text(f"{task.post.source_name} · {task.status_label}")
        status.set_tooltip_text(task.error or str(task.path))
        if task.status == "downloading":
            progress.set_visible(True)
            progress.set_fraction(task.progress)
            action.set_icon_name("process-stop-symbolic")
            action.set_tooltip_text("Cancel download")
        elif task.status == "queued":
            progress.set_visible(True)
            progress.set_fraction(0.0)
            progress.pulse()
            action.set_icon_name("process-stop-symbolic")
            action.set_tooltip_text("Cancel download")
        elif task.status == "done":
            progress.set_visible(False)
            action.set_icon_name("document-open-symbolic")
            action.set_tooltip_text("Open the downloaded file")
        elif task.status == "failed":
            progress.set_visible(False)
            action.set_icon_name("view-refresh-symbolic")
            action.set_tooltip_text("Retry download")
        else:
            progress.set_visible(False)
            action.set_icon_name("view-refresh-symbolic")
            action.set_tooltip_text("Download again")

    def _on_action_clicked(self, _button, task) -> None:
        if task.status == "done":
            self._open_file(task)
        elif task.status in ("failed", "cancelled"):
            self.manager.enqueue(task.post)
            self.on_notice("Retrying download…")
        else:
            self.manager.cancel(task)

    def _status_icon(self, status: str) -> str:
        return {
            "queued": "folder-download-symbolic",
            "downloading": "folder-download-symbolic",
            "done": "emblem-ok-symbolic",
            "failed": "dialog-warning-symbolic",
            "cancelled": "process-stop-symbolic",
        }.get(status, "folder-download-symbolic")

    def _update_state(self) -> None:
        has_rows = bool(self._rows)
        self.stack.set_visible_child_name("list" if has_rows else "empty")
        active = self.manager.active_count
        done = sum(1 for task in self.manager.tasks if task.status == "done")
        if active:
            self.subtitle.set_text(f"{active} downloading · {done} finished")
        elif done:
            self.subtitle.set_text(f"{done} downloaded")
        else:
            self.subtitle.set_text("Your saved artwork")
        self.clear_button.set_sensitive(any(not task.active for task in self.manager.tasks))

    # -- actions ---------------------------------------------------------
    def _open_file(self, task) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(task.path.as_uri(), None)
        except GLib.Error as exc:
            self.on_notice(f"Could not open file: {exc.message}")

    def _open_folder(self, _button) -> None:
        directory = Path(str(self.settings.get("download_dir"))).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(directory.as_uri(), None)
        except (OSError, GLib.Error) as exc:
            self.on_notice(f"Could not open folder: {exc}")

    def _clear_finished(self, _button) -> None:
        removed = self.manager.clear_finished()
        self.refresh()
        if removed:
            self.on_notice(f"Cleared {removed} finished download{'s' if removed != 1 else ''}")
