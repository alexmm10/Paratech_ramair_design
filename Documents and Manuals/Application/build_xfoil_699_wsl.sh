#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 0 ]; then
  PROJECT_ROOT="$1"
else
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
SOURCE_ZIP="$PROJECT_ROOT/Application Support/Tools/xfoil/source/Xfoil699src.zip"
SOURCE_PATCH="$PROJECT_ROOT/Application Support/Tools/xfoil/source/xfoil699-gfortran-eof.patch"
OUTPUT="$PROJECT_ROOT/Application Support/Tools/xfoil/linux/xfoil"
BUILD_ROOT="${TMPDIR:-/tmp}/ramair_xfoil_build"

for command_name in python3 patch make gfortran gcc; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "MISSING: $command_name" >&2
    echo "Install build tools with: sudo apt install -y python3 patch make gfortran gcc libx11-dev" >&2
    exit 2
  }
done

test -f "$SOURCE_ZIP" || { echo "MISSING: $SOURCE_ZIP" >&2; exit 2; }
test -f "$SOURCE_PATCH" || { echo "MISSING: $SOURCE_PATCH" >&2; exit 2; }

rm -rf "$BUILD_ROOT"
mkdir -p "$BUILD_ROOT" "$(dirname "$OUTPUT")"
python3 - "$SOURCE_ZIP" "$BUILD_ROOT" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2])
PY
SOURCE_ROOT="$BUILD_ROOT/Xfoil699src"

patch -d "$BUILD_ROOT" -p0 < "$SOURCE_PATCH"

# The distributed makefiles target pre-gfortran compilers.  Keep the source
# algorithms unchanged while enabling the compatibility flag required by
# modern gfortran and the conventional underscore C/Fortran ABI.
cp "$SOURCE_ROOT/plotlib/config.make.gfortran" "$SOURCE_ROOT/plotlib/config.make"
sed -i 's/^FFLAGS  *=.*/FFLAGS = -O2 -fallow-argument-mismatch/' "$SOURCE_ROOT/plotlib/config.make"
sed -i 's/^CFLAGS  *=.*/CFLAGS = -O2 -DUNDERSCORE -I\/usr\/include/' "$SOURCE_ROOT/plotlib/config.make"
sed -i 's#^LINKLIB *=.*#LINKLIB = -L/usr/lib/x86_64-linux-gnu -lX11#' "$SOURCE_ROOT/plotlib/config.make"
make -C "$SOURCE_ROOT/plotlib" clean >/dev/null
make -C "$SOURCE_ROOT/plotlib" -j"$(nproc)" >/dev/null

sed -i 's/^FC = gfortran -m64.*/FC = gfortran -m64/' "$SOURCE_ROOT/bin/Makefile.gfortran"
sed -i 's/^FFLAGS = -O2 -fomit-frame-pointer.*/FFLAGS = -O2 -fomit-frame-pointer -fallow-argument-mismatch/' "$SOURCE_ROOT/bin/Makefile.gfortran"
sed -i 's/^FFLOPT = -O3 -fomit-frame-pointer.*/FFLOPT = -O3 -fomit-frame-pointer -fallow-argument-mismatch/' "$SOURCE_ROOT/bin/Makefile.gfortran"
sed -i 's#^PLTOBJ = /usr/local/lib/libPlt_gfortran.a#PLTOBJ = ../plotlib/libPlt_gfortran.a#' "$SOURCE_ROOT/bin/Makefile.gfortran"
make -C "$SOURCE_ROOT/bin" -f Makefile.gfortran clean >/dev/null
make -C "$SOURCE_ROOT/bin" -f Makefile.gfortran xfoil -j"$(nproc)" >/dev/null

install -m 0755 "$SOURCE_ROOT/bin/xfoil" "$OUTPUT"
echo "OK: built $OUTPUT"
"$OUTPUT" <<'EOF' >/dev/null
QUIT
EOF
echo "OK: XFOIL startup/exit smoke test"
