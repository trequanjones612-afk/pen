#!/usr/bin/env bash
# Attempt a native MinGW build from an MSYS2 UCRT64, MINGW64, or CLANG64 shell.
#
# This is expected to fail. Those environments produce ordinary Windows
# binaries and do not provide fork(), termios, or the other POSIX pieces
# NeoMutt calls directly. The Linux equivalent of this attempt is
# build-mingw-cross.sh; its compiler output is in
# ../evidence/mingw-cross-errors.txt.
#
# MSYS2 is deprecating the MINGW64 environment in favor of UCRT64. Neither one
# has a neomutt package. The only MSYS2 package is msys/neomutt, built with
# the MSYS runtime. Use build-msys2.sh for that.

set -eu

case "${MSYSTEM:-}" in
  MSYS)
    echo "This shell is MSYS. NeoMutt builds here. Run build-msys2.sh."
    exit 2
    ;;
  UCRT64)
    PKG_PREFIX="mingw-w64-ucrt-x86_64"
    ;;
  MINGW64)
    PKG_PREFIX="mingw-w64-x86_64"
    ;;
  CLANG64)
    PKG_PREFIX="mingw-w64-clang-x86_64"
    ;;
  CLANGARM64)
    PKG_PREFIX="mingw-w64-clang-aarch64"
    ;;
  *)
    echo "Open an MSYS2 UCRT64 or MINGW64 shell first. MSYSTEM=${MSYSTEM:-unset}."
    exit 2
    ;;
esac

echo "MSYSTEM=$MSYSTEM"
echo "This probe installs ${PKG_PREFIX} ncurses and libiconv, then runs the NeoMutt build."
echo "A finished neomutt.exe from this compiler would be a native Windows program."
echo "The build is expected to stop on POSIX APIs this CRT does not have."

pacman -Sy --noconfirm
pacman -S --needed --noconfirm \
  git \
  make \
  "${PKG_PREFIX}-gcc" \
  "${PKG_PREFIX}-pkgconf" \
  "${PKG_PREFIX}-ncurses" \
  "${PKG_PREFIX}-libiconv"

REF="${NEOMUTT_REF:-69f25882271a266ef5ff3d1495e4a408208425e8}"
SRC_DIR="${NEOMUTT_SRC_DIR:-$HOME/src/neomutt-mingw-probe}"

if [ ! -d "$SRC_DIR/.git" ]; then
  git clone https://github.com/neomutt/neomutt.git "$SRC_DIR"
fi
cd "$SRC_DIR"
git fetch --depth 1 origin "$REF"
git checkout -f "$REF"

set +e
./configure \
  --prefix=/usr/local \
  --disable-doc \
  --disable-nls \
  --disable-idn2 \
  --disable-pgp \
  --disable-smime
cfg=$?
if [ "$cfg" -ne 0 ]; then
  echo
  echo "configure failed with exit $cfg under $MSYSTEM."
  echo "That is the expected result for a native MinGW-style build."
  exit "$cfg"
fi

make -k -j"$(nproc)"
mk=$?
if [ -f neomutt.exe ] || [ -f neomutt ]; then
  echo "Unexpected: a binary was produced. Inspect it with ldd / objdump before treating it as native."
  exit 0
fi
echo
echo "make failed with exit $mk and did not produce neomutt.exe."
echo "That is the expected result. Use the MSYS shell and build-msys2.sh."
exit "$mk"
