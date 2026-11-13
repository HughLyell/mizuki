"""Tests for the AppImage environment scrubbing used when launching external
programs (the cause of "Failed to execute child process gio-launch-desktop")."""

from __future__ import annotations

import unittest

from mizuki.ui.launch_env import clean_appimage_env


class CleanAppImageEnvTests(unittest.TestCase):
    def test_appimage_paths_are_removed(self) -> None:
        env = {
            "APPDIR": "/tmp/Mizuki.AppDir",
            "LD_LIBRARY_PATH": "/tmp/Mizuki.AppDir/usr/lib",
            "LD_PRELOAD": "/tmp/Mizuki.AppDir/usr/lib/libfoo.so",
            "GTK_PATH": "/tmp/Mizuki.AppDir/usr/lib/gtk-4.0",
            "GTK_EXE_PREFIX": "/tmp/Mizuki.AppDir/usr",
            "GDK_PIXBUF_MODULE_FILE": "/tmp/Mizuki.AppDir/usr/lib/gdk-pixbuf/loaders.cache",
            "GIO_MODULE_DIR": "/tmp/Mizuki.AppDir/usr/lib/gio/modules",
            "GI_TYPELIB_PATH": "/tmp/Mizuki.AppDir/usr/lib/girepository-1.0",
            "PATH": "/tmp/Mizuki.AppDir/usr/bin:/usr/bin:/bin",
            "XDG_DATA_DIRS": "/tmp/Mizuki.AppDir/usr/share:/usr/share",
            "DISPLAY": ":0",
        }
        cleaned = clean_appimage_env(env)
        for var in (
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "GTK_PATH",
            "GTK_EXE_PREFIX",
            "GDK_PIXBUF_MODULE_FILE",
            "GIO_MODULE_DIR",
            "GI_TYPELIB_PATH",
        ):
            self.assertNotIn(var, cleaned)
        self.assertEqual(cleaned["PATH"], "/usr/bin:/bin")
        self.assertEqual(cleaned["XDG_DATA_DIRS"], "/usr/share")

    def test_display_and_session_vars_are_preserved(self) -> None:
        env = {
            "APPDIR": "/tmp/Mizuki.AppDir",
            "PATH": "/tmp/Mizuki.AppDir/usr/bin:/usr/bin",
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }
        cleaned = clean_appimage_env(env)
        self.assertEqual(cleaned["DISPLAY"], ":0")
        self.assertEqual(cleaned["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(cleaned["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")

    def test_path_that_is_only_the_appdir_is_dropped(self) -> None:
        cleaned = clean_appimage_env({"APPDIR": "/tmp/App", "PATH": "/tmp/App/usr/bin"})
        self.assertNotIn("PATH", cleaned)

    def test_no_appdir_leaves_unrelated_values_alone(self) -> None:
        env = {"PATH": "/usr/bin:/bin", "DISPLAY": ":1"}
        cleaned = clean_appimage_env(env)
        self.assertEqual(cleaned["PATH"], "/usr/bin:/bin")
        self.assertEqual(cleaned["DISPLAY"], ":1")

    def test_original_mapping_is_not_mutated(self) -> None:
        env = {"APPDIR": "/tmp/App", "LD_LIBRARY_PATH": "/tmp/App/lib"}
        clean_appimage_env(env)
        self.assertIn("LD_LIBRARY_PATH", env)


if __name__ == "__main__":
    unittest.main()
