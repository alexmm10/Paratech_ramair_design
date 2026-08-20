#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"
while [ "$ROOT" != "/" ] && { [ ! -f "$ROOT/preprocess_ramair_main.py" ] || [ ! -d "$ROOT/CFD_2D" ]; }; do
  ROOT="$(dirname "$ROOT")"
done
if [ ! -f "$ROOT/preprocess_ramair_main.py" ] || [ ! -d "$ROOT/CFD_2D" ]; then
  echo "MISSING RamAir DESIGN APP root above $SCRIPT_DIR" >&2
  exit 2
fi

SOURCE="$ROOT/Application Support/Tools/xfoil/linux/xfoil"
TARGET_DIR="${HOME}/.local/bin"
TARGET="$TARGET_DIR/xfoil"

if [ ! -f "$SOURCE" ]; then
  echo "MISSING bundled XFOIL runtime: $SOURCE" >&2
  echo "Install with: sudo apt update && sudo apt install -y xfoil" >&2
  echo "Alternatively set RAMAIR_XFOIL_EXECUTABLE to a working executable." >&2
  exit 2
fi
mkdir -p "$TARGET_DIR"
install -m 0755 "$SOURCE" "$TARGET"
VERSION=$(printf '\nQUIT\n' | "$TARGET" 2>&1 | sed -n 's/.*XFOIL Version[[:space:]]*//p' | head -n 1)
echo "OK XFOIL ${VERSION:-unknown} installed at $TARGET"
echo "Complete matching source: $ROOT/Application Support/Tools/xfoil/source/Xfoil699src.zip"
