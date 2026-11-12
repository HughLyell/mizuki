"""Application object and global actions."""

from __future__ import annotations

import logging
import os
import time
import traceback
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import APP_NAME  # noqa: E402
from . import config  # noqa: E402
from .favorites import FavoriteStore  # noqa: E402
from .models import Post  # noqa: E402
from .services.downloads import DownloadManager  # noqa: E402
from .services.search import SearchController  # noqa: E402
from .services.thumbnail_loader import ThumbnailLoader  # noqa: E402
from .settings import Settings  # noqa: E402
from .sources import SourceRegistry  # noqa: E402
from .ui.detail import ImageDetailDialog  # noqa: E402
from .ui.preferences import PreferencesDialog  # noqa: E402
from .ui.theme import ThemeManager  # noqa: E402
from .ui.window import MizukiWindow  # noqa: E402

log = logging.getLogger(__name__)


class MizukiApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id="io.github.hughlyell.Mizuki",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._start_time = time.perf_counter()
        self.settings = Settings()
        self.favorites = FavoriteStore()
        self.registry = SourceRegistry()
        self.loader = ThumbnailLoader()
        self.search_controller = SearchController()
        self.downloads = DownloadManager(self.settings)
        self.theme = ThemeManager()
        self.window: MizukiWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        config.ensure_app_dirs()
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname("io.github.hughlyell.Mizuki")
        self._install_actions()
        self.loader.start_maintenance()
        self._install_theme()

    def do_activate(self) -> None:
        self._install_theme()
        if self.window is None:
            self.window = MizukiWindow(self)
        self.window.present()
        if log.isEnabledFor(logging.DEBUG):
            elapsed = (time.perf_counter() - self._start_time) * 1000
            try:
                process_start = float(os.environ.get("MIZUKI_PROCESS_START", "0"))
            except ValueError:
                process_start = 0.0
            total = (time.perf_counter() - process_start) * 1000 if process_start else 0.0
            log.debug("Mizuki window ready in %.0f ms (UI), %.0f ms (total)", elapsed, total)
        if os.environ.get("MIZUKI_SMOKE_TEST") == "1":
            GLib.timeout_add(900, self._run_smoke_test)
        elif os.environ.get("MIZUKI_DEMO") == "1":
            self._demo_started = time.monotonic()
            self._demo_dir = Path(
                os.environ.get("MIZUKI_DEMO_DIR", str(config.CACHE_DIR / "gui-check" / "demo"))
            )
            self._demo_step = 0
            self._demo_dialog = None
            self._demo_download = None
            self._demo_waiting = False
            self._demo_original_source = (
                self.window.discover.current_source_id() if self.window else "danbooru"
            )
            GLib.timeout_add(500, self._demo_tick)
        elif os.environ.get("MIZUKI_SCREENSHOT"):
            try:
                delay = max(1, int(os.environ.get("MIZUKI_SCREENSHOT_DELAY", "5")))
            except ValueError:
                delay = 5
            GLib.timeout_add(delay * 1000, self._capture_screenshot)

    def _snapshot_widget(self, widget, path: Path) -> None:
        width = max(1, widget.get_width())
        height = max(1, widget.get_height())
        paintable = Gtk.WidgetPaintable.new(widget)
        node = None
        for _attempt in range(5):
            snapshot = Gtk.Snapshot.new()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is not None:
                break
            time.sleep(0.08)
        if node is None:
            raise RuntimeError("Widget produced an empty render node")
        renderer = Gsk.CairoRenderer.new()
        display = Gdk.Display.get_default()
        if display is not None and hasattr(renderer, "realize_for_display"):
            renderer.realize_for_display(display)
        rect = Graphene.Rect()
        rect.init(0, 0, width, height)
        texture = renderer.render_texture(node, rect)
        if texture is None or not hasattr(texture, "save_to_png"):
            raise RuntimeError(f"Unexpected snapshot texture: {type(texture).__name__}")
        path.parent.mkdir(parents=True, exist_ok=True)
        texture.save_to_png(str(path))

    def _demo_tick(self):
        """Drive a short visual walkthrough and save screenshots."""
        try:
            window = self.window
            if window is None:
                raise RuntimeError("No window")
            elapsed = time.monotonic() - self._demo_started

            if self._demo_step == 0:
                if window.discover.grid.count == 0 and elapsed < 25:
                    return True
                GLib.timeout_add(1800, self._demo_advance)
                self._demo_step = 1
                return False

            if self._demo_step == 1:
                self._snapshot_widget(window, self._demo_dir / "01-main.png")
                posts = window.discover.grid.posts
                if posts:
                    dialog = ImageDetailDialog(
                        posts[0],
                        window.loader,
                        window.favorites,
                        on_download=window._download_post,
                        on_favorite=window._toggle_favorite,
                        on_tag_activate=window._search_tag,
                    )
                    self._demo_dialog = dialog
                    dialog.present(window)
                    GLib.timeout_add(2500, self._demo_advance)
                else:
                    GLib.timeout_add(100, self._demo_advance)
                self._demo_step = 2
                return False

            if self._demo_step == 2:
                if self._demo_dialog is not None:
                    self._snapshot_widget(self._demo_dialog, self._demo_dir / "02-detail.png")
                    self._demo_dialog.close()
                    self._demo_dialog = None
                GLib.timeout_add(400, self._demo_advance)
                self._demo_step = 3
                return False

            if self._demo_step == 3:
                window.show_preferences()
                GLib.timeout_add(900, self._demo_advance)
                self._demo_step = 4
                return False

            if self._demo_step == 4:
                if window._preferences is not None:
                    self._snapshot_widget(window._preferences, self._demo_dir / "03-preferences.png")
                    window._preferences.close()
                    window._preferences = None
                window.switch_page("favorites")
                GLib.timeout_add(500, self._demo_advance)
                self._demo_step = 5
                return False

            if self._demo_step == 5:
                self._snapshot_widget(window, self._demo_dir / "04-favorites.png")
                posts = window.discover.grid.posts
                if posts:
                    demo_post = replace(
                        posts[0],
                        id=f"demo-{int(time.time())}",
                        file_url=posts[0].thumb_url,
                        ext="jpg",
                    )
                    self._demo_download = window.downloads.enqueue(demo_post)
                window.switch_page("downloads")
                GLib.timeout_add(500, self._demo_advance)
                self._demo_step = 6
                return False

            if self._demo_step == 6:
                if self._demo_download is not None and self._demo_download.active:
                    GLib.timeout_add(400, self._demo_advance)
                    return False
                self._snapshot_widget(window, self._demo_dir / "05-downloads.png")
                if self._demo_download is not None:
                    print(
                        f"MIZUKI DEMO DOWNLOAD: {self._demo_download.status} "
                        f"{self._demo_download.path}",
                        flush=True,
                    )
                    try:
                        self._demo_download.path.unlink(missing_ok=True)
                    except OSError:
                        pass
                target = "zerochan" if window.discover.current_source_id() != "zerochan" else "safebooru"
                wrapper = window._source_rows.get(target)
                if wrapper is not None:
                    window._on_source_row_activated(window.source_list, wrapper)
                else:
                    window._on_source_selected(target, run=True)
                GLib.timeout_add(3500, self._demo_advance)
                self._demo_step = 7
                return False

            if self._demo_step == 7:
                self._snapshot_widget(window, self._demo_dir / "06-source.png")
                window.discover.search_entry.set_text("landscape")
                window.discover.run_search(reset=True, record=False)
                GLib.timeout_add(3500, self._demo_advance)
                self._demo_step = 8
                return False

            if self._demo_step == 8:
                self._snapshot_widget(window, self._demo_dir / "07-search.png")
                window.discover.search_entry.set_text("")
                window._on_source_selected(self._demo_original_source, run=False)
                print(f"MIZUKI DEMO: OK ({self._demo_dir})", flush=True)
                GLib.timeout_add(200, self.quit)
                return False
        except Exception:
            traceback.print_exc()
            print("MIZUKI DEMO: FAIL", flush=True)
            self.quit()
        return False

    def _demo_advance(self):
        GLib.timeout_add(100, self._demo_tick)
        return False


    def _capture_screenshot(self):
        """Save the rendered window to PNG, then quit.

        Useful for visual regression checks on a real display or under Xvfb.
        Set ``MIZUKI_SCREENSHOT=/path/window.png`` and optionally
        ``MIZUKI_SCREENSHOT_DELAY=5``.
        """
        path = Path(os.environ["MIZUKI_SCREENSHOT"]).expanduser()
        try:
            if self.window is None:
                raise RuntimeError("No application window")
            self._snapshot_widget(self.window, path)
            print(f"MIZUKI SCREENSHOT: {path}", flush=True)
        except Exception:
            traceback.print_exc()
            print("MIZUKI SCREENSHOT: FAIL", flush=True)
        self.quit()
        return False

    def _run_smoke_test(self):
        """Build and exercise every major widget without relying on the network.

        Run with ``./run.sh --smoke-test``. This catches GTK/libadwaita API
        mistakes that unit tests cannot see, without needing synthetic input.
        """
        try:
            window = self.window
            if window is None:
                raise RuntimeError("No application window")
            for page in ("favorites", "downloads", "discover"):
                window.switch_page(page)

            sample = Post(
                id="smoke-test",
                source="local",
                source_name="Smoke test",
                thumb_url="",
                preview_url="",
                file_url="",
                page_url="",
                width=1920,
                height=1080,
                tags=("landscape", "smoke_test"),
                rating="safe",
                artist="Mizuki",
                ext="jpg",
            )
            window.discover.grid.append([sample])
            window.discover._stack.set_visible_child_name("grid")

            detail = ImageDetailDialog(
                sample,
                window.loader,
                window.favorites,
                on_download=window._download_post,
                on_favorite=window._toggle_favorite,
                on_tag_activate=window._search_tag,
            )
            detail.present(window)
            detail.close()

            preferences = PreferencesDialog(
                window.settings,
                window.registry,
                on_request_safe=window.request_safe_search,
                on_notice=window.toast,
            )
            preferences.present(window)
            preferences.close()

            window.favorites_page.refresh()
            window.downloads_page.refresh()
            window._apply_accent()
            window._apply_color_scheme()
        except Exception:
            traceback.print_exc()
            print("MIZUKI GUI SMOKE: FAIL", flush=True)
            self.quit()
            return False
        print("MIZUKI GUI SMOKE: OK", flush=True)
        GLib.timeout_add(150, self._quit_smoke_test)
        return False

    def _quit_smoke_test(self):
        self.quit()
        return False

    def do_shutdown(self) -> None:
        try:
            self.downloads.shutdown()
            self.search_controller.shutdown()
            self.loader.shutdown()
        finally:
            Adw.Application.do_shutdown(self)

    # -- setup -----------------------------------------------------------
    def _install_theme(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        accent = self.settings.get("accent", "mauve")
        dark = Adw.StyleManager.get_default().get_dark()
        self.theme.install(display, accent=accent, dark=dark)
        self._theme_installed = True

    def _install_actions(self) -> None:
        actions = [
            ("preferences", self._show_preferences, ["<Control>comma"]),
            ("about", self._show_about, ["F1"]),
            ("quit", self._quit, ["<Control>q"]),
            ("search", self._focus_search, ["<Control>f"]),
            ("refresh", self._refresh, ["<Control>r"]),
            ("safe-search", self._toggle_safe, ["<Control>s"]),
            ("downloads", self._show_downloads, ["<Control>d"]),
            ("favorites", self._show_favorites, ["<Control>k"]),
        ]
        for name, callback, accels in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    # -- action handlers -------------------------------------------------
    def _show_preferences(self, _action, _param) -> None:
        if self.window is not None:
            self.window.show_preferences()

    def _show_about(self, _action, _param) -> None:
        if self.window is not None:
            self.window.show_about()

    def _quit(self, _action, _param) -> None:
        self.quit()

    def _focus_search(self, _action, _param) -> None:
        if self.window is not None:
            self.window.focus_search()

    def _refresh(self, _action, _param) -> None:
        if self.window is not None:
            self.window.refresh_page()

    def _toggle_safe(self, _action, _param) -> None:
        if self.window is not None:
            self.window.toggle_safe()

    def _show_downloads(self, _action, _param) -> None:
        if self.window is not None:
            self.window.switch_page("downloads")

    def _show_favorites(self, _action, _param) -> None:
        if self.window is not None:
            self.window.switch_page("favorites")
