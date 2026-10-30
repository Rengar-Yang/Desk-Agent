#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3)"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/orbit-codex"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

"$PYTHON_BIN" -c 'import PyQt5.QtWidgets' || {
  echo "当前 Python 缺少 PyQt5。请安装 python3-pyqt5，或用已有 PyQt5 的 Python 运行 install.sh。" >&2
  exit 1
}
command -v xprop >/dev/null
command -v xwininfo >/dev/null

mkdir -p "$INSTALL_ROOT" "$APPLICATIONS_DIR" "$AUTOSTART_DIR"
install -m 755 "$APP_ROOT/native_app.py" "$INSTALL_ROOT/native_app.py"
if [ -f "$APP_ROOT/api.txt" ]; then
  ln -sfn "$APP_ROOT/api.txt" "$INSTALL_ROOT/api.txt"
fi
cat > "$INSTALL_ROOT/orbit-codex" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_BIN" "$INSTALL_ROOT/native_app.py" "\$@"
EOF
chmod 755 "$INSTALL_ROOT/orbit-codex"
cat > "$INSTALL_ROOT/orbit-codex.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Orbit Codex
Comment=Linux desktop companion for local Codex
Exec=$INSTALL_ROOT/orbit-codex
Terminal=false
Categories=Utility;Development;
StartupNotify=false
EOF
install -m 644 "$INSTALL_ROOT/orbit-codex.desktop" "$APPLICATIONS_DIR/orbit-codex.desktop"
install -m 644 "$INSTALL_ROOT/orbit-codex.desktop" "$AUTOSTART_DIR/orbit-codex.desktop"

echo "已安装 Orbit Codex，并启用登录后自动启动。"
echo "现在运行：$INSTALL_ROOT/orbit-codex"
