"""Application object and global actions."""

from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

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
            log.debug("Mizuki window ready in %.0f ms", elapsed)
        if os.environ.get("MIZUKI_SMOKE_TEST") == "1":
            GLib.timeout_add(900, self._run_smoke_test)
        elif os.environ.get("MIZUKI_SCREENSHOT"):
            try:
                delay = max(1, int(os.environ.get("MIZUKI_SCREENSHOT_DELAY", "5")))
            except ValueError:
                delay = 5
            GLib.timeout_add(delay * 1000, self._capture_screenshot)

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
            paintable = Gtk.WidgetPaintable.new(self.window)
            image = paintable.get_current_image()
            if image is None or not hasattr(image, "save_to_png"):
                raise RuntimeError(f"Unexpected paintable snapshot: {type(image).__name__}")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save_to_png(str(path))
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
