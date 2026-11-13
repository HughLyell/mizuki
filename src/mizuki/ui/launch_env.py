"""Environment handling for launching external programs.

Deliberately free of GI imports so it can be unit-tested on a headless machine.

When Mizuki runs from its AppImage, the runtime exports paths for the bundled
GTK/GLib so the app itself works. A child process (a browser, a file manager)
must not inherit them, or it will try to load the AppImage's incompatible
libraries and modules and fail to start.
"""

from __future__ import annotations

from collections.abc import Mapping

# Variables the AppImage runtime injects for Mizuki itself.
APPENV_VARS = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "GTK_PATH",
    "GTK_EXE_PREFIX",
    "GTK_DATA_PREFIX",
    "GTK_MODULES",
    "GDK_PIXBUF_MODULE_FILE",
    "GDK_PIXBUF_MODULEDIR",
    "GIO_MODULE_DIR",
    "GIO_EXTRA_MODULES",
    "GSETTINGS_SCHEMA_DIR",
    "GI_TYPELIB_PATH",
    "GST_PLUGIN_PATH",
    "GST_PLUGIN_SCANNER",
    "FONTCONFIG_FILE",
)
# Colon-separated variables that hold an AppDir entry first.
APPENV_PATH_VARS = ("PATH", "XDG_DATA_DIRS")


def clean_appimage_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``environ`` without the AppImage's injected paths.

    Display and session variables are preserved so the launched program can
    still connect to the user's desktop.
    """
    env = dict(environ)
    appdir = env.get("APPDIR", "")
    for var in APPENV_VARS:
        env.pop(var, None)
    if appdir:
        for var in APPENV_PATH_VARS:
            value = env.get(var)
            if not value:
                continue
            parts = [part for part in value.split(":") if part and not part.startswith(appdir)]
            if parts:
                env[var] = ":".join(parts)
            else:
                env.pop(var, None)
    return env
