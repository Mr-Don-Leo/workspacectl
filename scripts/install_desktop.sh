#!/usr/bin/env bash
# Install "Developer Workspaces" into the user's application menu.
# Usage: scripts/install_desktop.sh  (from a git checkout; no root needed)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="io.github.MrDonLeo.DevWorkspaceManager"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

# prefer an installed `devws`; otherwise launch straight from this checkout
if command -v devws >/dev/null 2>&1; then
  EXEC_CMD="devws"
else
  EXEC_CMD="$(command -v python3) -m devws"
fi

mkdir -p "$APPS_DIR" "$ICON_DIR"
sed "s|^Exec=.*|Exec=env PYTHONPATH=$ROOT $EXEC_CMD|" \
  "$ROOT/packaging/$APP_ID.desktop" > "$APPS_DIR/$APP_ID.desktop"
cp "$ROOT/packaging/$APP_ID.svg" "$ICON_DIR/$APP_ID.svg"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t \
  "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

echo "Installed: $APPS_DIR/$APP_ID.desktop"
echo "Launch 'Developer Workspaces' from your app menu."
