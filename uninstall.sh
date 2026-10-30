#!/usr/bin/env bash
set -euo pipefail
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/orbit-codex"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
rm -f "$APPLICATIONS_DIR/orbit-codex.desktop" "$AUTOSTART_DIR/orbit-codex.desktop"
rm -rf "$INSTALL_ROOT"
echo "已移除 Orbit Codex 程序和自启动项。用户设置仍在 ~/.config/orbit-codex。"
