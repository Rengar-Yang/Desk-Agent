#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
page_url="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).as_uri())' "$project_dir/index.html")"

if command -v firefox >/dev/null 2>&1; then
  exec firefox --new-window "$page_url"
elif command -v chromium >/dev/null 2>&1; then
  exec chromium "$page_url"
elif command -v google-chrome >/dev/null 2>&1; then
  exec google-chrome "$page_url"
else
  printf '没有找到可用浏览器。请将这个地址复制到浏览器地址栏：\n%s\n' "$page_url" >&2
  exit 1
fi
