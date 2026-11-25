#!/usr/bin/env bash
# Reproduce the failed native Windows cross-build from Linux.
#
# Target: x86_64-w64-mingw32 (MinGW-w64). This is the same kind of toolchain
# as MSYS2 UCRT64 / MINGW64: a Windows PE binary with no msys-2.0.dll and no
# cygwin1.dll. NeoMutt does not have a port to that target.
#
# The script builds ncurses and libiconv first, because those are ordinary
# missing libraries, then configures NeoMutt. It adds a PCRE2 <regex.h> shim
# only so the compiler gets past the first missing header and reports the
# POSIX errors underneath. The shim is not a port, and this script does not
# produce neomutt.exe.
#
# Needs: x86_64-w64-mingw32-gcc, make, curl, tar, a C toolchain for the
# build machine. On Debian/Ubuntu: gcc-mingw-w64-x86-64.

set -euo pipefail

if ! command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
  echo "x86_64-w64-mingw32-gcc is not on PATH."
  exit 1
fi

REF="${NEOMUTT_REF:-69f25882271a266ef5ff3d1495e4a408208425e8}"
WORK="${NEOMUTT_MINGW_WORK:-/tmp/neomutt-mingw-probe}"
PREFIX="$WORK/deps"
SRC="$WORK/src"
mkdir -p "$SRC"

fetch() {
  local url="$1" dest="$2"
  if [ ! -f "$dest" ]; then
    curl -fL --retry 3 -o "$dest" "$url"
  fi
}

if [ ! -f "$PREFIX/lib/libncursesw.a" ]; then
  fetch https://ftp.gnu.org/gnu/ncurses/ncurses-6.5.tar.gz "$SRC/ncurses-6.5.tar.gz"
  rm -rf "$SRC/ncurses-6.5"
  tar -xzf "$SRC/ncurses-6.5.tar.gz" -C "$SRC"
  (
    cd "$SRC/ncurses-6.5"
    ./configure \
      --host=x86_64-w64-mingw32 \
      --prefix="$PREFIX" \
      --enable-widec \
      --without-cxx --without-cxx-binding --without-ada \
      --without-tests --without-debug \
      --disable-db-install --disable-termcap \
      --enable-term-driver --enable-sp-funcs \
      --without-progs --disable-home-terminfo
    make -j"$(nproc)"
    make install
  )
fi

if [ ! -f "$PREFIX/lib/libiconv.a" ]; then
  fetch https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.18.tar.gz "$SRC/libiconv-1.18.tar.gz"
  rm -rf "$SRC/libiconv-1.18"
  tar -xzf "$SRC/libiconv-1.18.tar.gz" -C "$SRC"
  (
    cd "$SRC/libiconv-1.18"
    ./configure --host=x86_64-w64-mingw32 --prefix="$PREFIX" \
      --disable-nls --enable-static --disable-shared
    make -j"$(nproc)"
    make install
  )
fi

if [ ! -f "$PREFIX/lib/libpcre2-posix.a" ]; then
  fetch https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.46/pcre2-10.46.tar.gz \
    "$SRC/pcre2-10.46.tar.gz"
  rm -rf "$SRC/pcre2-10.46"
  tar -xzf "$SRC/pcre2-10.46.tar.gz" -C "$SRC"
  (
    cd "$SRC/pcre2-10.46"
    ./configure --host=x86_64-w64-mingw32 --prefix="$PREFIX" \
      --disable-shared --enable-static \
      --enable-pcre2-8 --disable-pcre2-16 --disable-pcre2-32
    make -j"$(nproc)"
    make install
  )
fi

# MinGW-w64 ships no POSIX regex.h. This header only exists so later errors
# are visible. Do not ship it as a Windows port.
cat > "$PREFIX/include/regex.h" <<'EOF'
/* Probe shim. MinGW-w64 has no POSIX regex.h. */
#include <pcre2posix.h>
EOF

if [ ! -d "$SRC/neomutt/.git" ]; then
  git clone https://github.com/neomutt/neomutt.git "$SRC/neomutt"
fi
cd "$SRC/neomutt"
git fetch --depth 1 origin "$REF"
git checkout -f "$REF"

# A clean tree each attempt. configure writes config.h in-tree.
git clean -fdx -e .git >/dev/null 2>&1 || true
git checkout -f "$REF"

./configure \
  --host=x86_64-w64-mingw32 \
  --prefix="$WORK/prefix" \
  --disable-doc \
  --disable-nls \
  --disable-idn2 \
  --disable-pgp \
  --disable-smime \
  --with-ncurses="$PREFIX" \
  --with-iconv="$PREFIX"

set +e
make -k -j"$(nproc)" EXTRA_LDFLAGS="-lpcre2-posix -lpcre2-8"
mk=$?
set -e

echo
if [ -f neomutt.exe ]; then
  echo "Unexpected: neomutt.exe exists. Check its imports before calling it native."
  x86_64-w64-mingw32-objdump -p neomutt.exe | grep "DLL Name" || true
  exit 0
fi
echo "Cross build failed with make exit $mk and produced no neomutt.exe."
echo "That is the expected result. See evidence/mingw-cross-errors.txt for the run this script was written from."
exit "$mk"
