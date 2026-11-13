#!/usr/bin/env bash
# Build a self-contained Linux AppImage for Mizuki.
#
# Intended for Ubuntu 24.04 (or the equivalent GitHub Actions runner), where
# GTK 4.12+, libadwaita 1.5+, PyGObject and PyInstaller are available.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$ROOT/build/appimage"
APPDIR="$BUILD/Mizuki.AppDir"
DIST="$ROOT/dist"
TOOLS="$BUILD/tools"
VERSION="$(sed -n "s/^__version__ = \"\(.*\)\"/\1/p" "$ROOT/src/mizuki/__init__.py")"
VERSION="${VERSION:-0.1.0}"
APP_ID="io.github.hughlyell.Mizuki"
# Target architecture for the linuxdeploy/appimagetool binaries and the output
# filenames. Defaults to the build machine's architecture.
ARCH="${ARCH:-$(uname -m)}"

mkdir -p "$BUILD" "$DIST" "$TOOLS"

# Validate the shipped metadata before doing any heavy lifting.
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$ROOT/data/$APP_ID.desktop"
fi
if command -v appstreamcli >/dev/null 2>&1; then
    appstreamcli validate --no-net "$ROOT/data/$APP_ID.metainfo.xml" \
        || echo "warning: appstreamcli validation reported issues" >&2
fi

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "pyinstaller is required (python3 -m pip install pyinstaller)" >&2
    exit 1
fi

rm -rf "$BUILD/pyinstaller" "$APPDIR"
mkdir -p "$BUILD/pyinstaller"
cd "$ROOT"

python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name mizuki \
    --distpath "$BUILD/pyinstaller/dist" \
    --workpath "$BUILD/pyinstaller/work" \
    --specpath "$BUILD/pyinstaller" \
    --paths "$ROOT/src" \
    --collect-all gi \
    --collect-submodules mizuki \
    --hidden-import mizuki.sources.danbooru \
    --hidden-import mizuki.sources.safebooru \
    --hidden-import mizuki.sources.moebooru \
    --hidden-import mizuki.sources.zerochan \
    --hidden-import gi.repository.Gtk \
    --hidden-import gi.repository.Adw \
    --hidden-import gi.repository.Gdk \
    "$ROOT/packaging/appimage/entry.py"

mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/mizuki"
cp -a "$BUILD/pyinstaller/dist/mizuki/." "$APPDIR/usr/bin/"
cp -a "$ROOT/data" "$APPDIR/usr/share/mizuki/data"
if [ -d /usr/share/icons/Adwaita ]; then
    mkdir -p "$APPDIR/usr/share/icons"
    cp -a /usr/share/icons/Adwaita "$APPDIR/usr/share/icons/"
fi
if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
    cp /etc/ssl/certs/ca-certificates.crt "$APPDIR/usr/share/mizuki/ca-certificates.crt"
fi
mkdir -p "$APPDIR/usr/bin/_internal/gi_typelibs" "$APPDIR/usr/lib/girepository-1.0"
while IFS= read -r -d '' typelib; do
    cp -a "$typelib" "$APPDIR/usr/bin/_internal/gi_typelibs/"
    cp -a "$typelib" "$APPDIR/usr/lib/girepository-1.0/"
done < <(find /usr/lib -path '*/girepository-1.0/*.typelib' -print0)
cp "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$ROOT/data/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
cp "$ROOT/data/$APP_ID.desktop" "$APPDIR/usr/share/applications/$APP_ID.desktop"
if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 256 -h 256 \
        "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" \
        -o "$APPDIR/$APP_ID.png"
    cp "$APPDIR/$APP_ID.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
else
    cp "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$APPDIR/$APP_ID.svg"
fi

# linuxdeploy and its GTK plugin bundle the native GTK/libadwaita runtime:
# schemas, pixbuf loaders, typelibs and the shared-library closure.
download() {
    local url="$1" output="$2"
    if [ ! -x "$output" ]; then
        wget -q -O "$output" "$url"
        chmod +x "$output"
    fi
}

download \
    "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${ARCH}.AppImage" \
    "$TOOLS/linuxdeploy-${ARCH}"
if [ ! -x "$TOOLS/linuxdeploy-plugin-gtk.sh" ]; then
    wget -q -O "$TOOLS/linuxdeploy-plugin-gtk.sh" \
        "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh"
    chmod +x "$TOOLS/linuxdeploy-plugin-gtk.sh"
fi
export LINUXDEPLOY="$TOOLS/linuxdeploy-${ARCH}"
export DEPLOY_GTK_VERSION=4

# AppImages need FUSE to self-mount; use the extract-and-run fallback in build
# containers where FUSE is unavailable.
LINUXDEPLOY_ARGS=(--appimage-extract-and-run)
"$LINUXDEPLOY" "${LINUXDEPLOY_ARGS[@]}" \
    --appdir "$APPDIR" \
    --plugin gtk

# linuxdeploy generates an AppRun; restore our wrapper so Mizuki's resources
# and the frozen executable are resolved from the AppImage.
cp "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

APPIMAGETOOL="$TOOLS/appimagetool-${ARCH}"
download \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${ARCH}.AppImage" \
    "$APPIMAGETOOL"

OUTPUT="$DIST/Mizuki-${VERSION}-${ARCH}.AppImage"
rm -f "$OUTPUT"
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUTPUT"
chmod +x "$OUTPUT"

# FUSE 2 is not installed by default on some current distributions. Ship a
# single-file self-extracting launcher as a no-FUSE fallback. It contains the
# same AppDir and runs through the same AppRun wrapper.
PORTABLE="$DIST/Mizuki-${VERSION}-${ARCH}.run"
rm -f "$PORTABLE"
cp "$ROOT/packaging/appimage/portable-runner" "$PORTABLE"
sed -i "s/@VERSION@/$VERSION/" "$PORTABLE"
tar -C "$APPDIR" -czf "$BUILD/mizuki-portable.tar.gz" .
cat "$BUILD/mizuki-portable.tar.gz" >> "$PORTABLE"
chmod +x "$PORTABLE"

echo "Built $OUTPUT"
echo "Built $PORTABLE"
