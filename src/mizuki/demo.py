"""Self-test and demo drivers: smoke test, screenshots, visual walkthrough.

These run only when the corresponding ``MIZUKI_*`` environment variables are
set (see ``main.py``) and live outside the application object so the
production startup path stays small.
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import config  # noqa: E402
from .models import Post  # noqa: E402
from .ui.detail import ImageDetailDialog  # noqa: E402
from .ui.preferences import PreferencesDialog  # noqa: E402


def snapshot_widget(widget, path: Path) -> None:
    """Render a widget to a PNG file."""
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


def schedule_screenshot(app, path: Path, delay_seconds: int) -> None:
    """Save the rendered window to PNG after ``delay_seconds``, then quit.

    Useful for visual regression checks on a real display or under Xvfb.
    Set ``MIZUKI_SCREENSHOT=/path/window.png`` and optionally
    ``MIZUKI_SCREENSHOT_DELAY=5``.
    """

    def _capture():
        try:
            if app.window is None:
                raise RuntimeError("No application window")
            snapshot_widget(app.window, path)
            print(f"MIZUKI SCREENSHOT: {path}", flush=True)
        except Exception:
            traceback.print_exc()
            print("MIZUKI SCREENSHOT: FAIL", flush=True)
        app.quit()
        return False

    GLib.timeout_add(delay_seconds * 1000, _capture)


def run_smoke_test(app):
    """Build and exercise every major widget without relying on the network.

    Run with ``./run.sh --smoke-test``. This catches GTK/libadwaita API
    mistakes that unit tests cannot see, without needing synthetic input.
    """
    try:
        window = app.window
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
        app.quit()
        return False
    print("MIZUKI GUI SMOKE: OK", flush=True)
    GLib.timeout_add(150, lambda *_: app.quit())
    return False


def start_demo(app) -> None:
    DemoDriver(app)


class DemoDriver:
    """Drive a short visual walkthrough and save screenshots."""

    def __init__(self, app) -> None:
        self.app = app
        self.started = time.monotonic()
        self.dir = Path(
            os.environ.get("MIZUKI_DEMO_DIR", str(config.CACHE_DIR / "gui-check" / "demo"))
        )
        self.step = 0
        self.dialog = None
        self.download = None
        window = app.window
        self.original_source = (
            window.discover.current_source_id() if window else "danbooru"
        )
        GLib.timeout_add(500, self._tick)

    def _tick(self):
        try:
            return self._step()
        except Exception:
            traceback.print_exc()
            print("MIZUKI DEMO: FAIL", flush=True)
            self.app.quit()
            return False

    def _step(self):
        app = self.app
        window = app.window
        if window is None:
            raise RuntimeError("No window")
        elapsed = time.monotonic() - self.started

        if self.step == 0:
            if window.discover.grid.count == 0 and elapsed < 25:
                return True
            GLib.timeout_add(1800, self._advance)
            self.step = 1
            return False

        if self.step == 1:
            snapshot_widget(window, self.dir / "01-main.png")
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
                self.dialog = dialog
                dialog.present(window)
                GLib.timeout_add(2500, self._advance)
            else:
                GLib.timeout_add(100, self._advance)
            self.step = 2
            return False

        if self.step == 2:
            if self.dialog is not None:
                snapshot_widget(self.dialog, self.dir / "02-detail.png")
                self.dialog.close()
                self.dialog = None
            GLib.timeout_add(400, self._advance)
            self.step = 3
            return False

        if self.step == 3:
            window.show_preferences()
            GLib.timeout_add(900, self._advance)
            self.step = 4
            return False

        if self.step == 4:
            if window._preferences is not None:
                snapshot_widget(window._preferences, self.dir / "03-preferences.png")
                window._preferences.close()
                window._preferences = None
            window.switch_page("favorites")
            GLib.timeout_add(500, self._advance)
            self.step = 5
            return False

        if self.step == 5:
            snapshot_widget(window, self.dir / "04-favorites.png")
            posts = window.discover.grid.posts
            if posts:
                demo_post = replace(
                    posts[0],
                    id=f"demo-{int(time.time())}",
                    file_url=posts[0].thumb_url,
                    ext="jpg",
                )
                self.download = window.downloads.enqueue(demo_post)
            window.switch_page("downloads")
            GLib.timeout_add(500, self._advance)
            self.step = 6
            return False

        if self.step == 6:
            if self.download is not None and self.download.active:
                GLib.timeout_add(400, self._advance)
                return False
            snapshot_widget(window, self.dir / "05-downloads.png")
            if self.download is not None:
                print(
                    f"MIZUKI DEMO DOWNLOAD: {self.download.status} "
                    f"{self.download.path}",
                    flush=True,
                )
                try:
                    self.download.path.unlink(missing_ok=True)
                except OSError:
                    pass
            target = "zerochan" if window.discover.current_source_id() != "zerochan" else "safebooru"
            wrapper = window._source_rows.get(target)
            if wrapper is not None:
                window._on_source_row_activated(window.source_list, wrapper)
            else:
                window._on_source_selected(target, run=True)
            GLib.timeout_add(3500, self._advance)
            self.step = 7
            return False

        if self.step == 7:
            snapshot_widget(window, self.dir / "06-source.png")
            window.discover.search_entry.set_text("landscape")
            window.discover.run_search(reset=True, record=False)
            GLib.timeout_add(3500, self._advance)
            self.step = 8
            return False

        if self.step == 8:
            snapshot_widget(window, self.dir / "07-search.png")
            window.discover.search_entry.set_text("")
            window._on_source_selected(self.original_source, run=False)
            print(f"MIZUKI DEMO: OK ({self.dir})", flush=True)
            GLib.timeout_add(200, app.quit)
            return False
        return False

    def _advance(self):
        GLib.timeout_add(100, self._tick)
        return False
