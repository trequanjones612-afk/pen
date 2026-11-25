#!/usr/bin/env bash
# Build NeoMutt inside MSYS2's MSYS environment and install it to /usr.
#
# Run this from the "MSYS2 MSYS" shell, or from PowerShell via run-neomutt.ps1 -Build.
# The UCRT64, MINGW64, and CLANG64 shells use a native MinGW toolchain. NeoMutt
# does not build with that toolchain. See try-ucrt64.sh and the README.
#
# This follows the MSYS2 package recipe:
#   https://github.com/msys2/MSYS2-packages/blob/master/neomutt/PKGBUILD
# The binary tested under Wine was neomutt 20260616-3 from the msys repo, which
# applies the OpenSSL 4 patch below on top of tag 20260616.

set -euo pipefail

if [ "${MSYSTEM:-}" != "MSYS" ]; then
  echo "This script has to run in the MSYS2 MSYS shell."
  echo "Current MSYSTEM=${MSYSTEM:-unset}."
  echo "Start menu: MSYS2 MSYS. From PowerShell: C:\\msys64\\usr\\bin\\bash.exe -lc ./build-msys2.sh"
  echo "UCRT64 / MINGW64 will not produce a native NeoMutt. See try-ucrt64.sh."
  exit 1
fi

REF="${NEOMUTT_REF:-20260616}"
SRC_DIR="${NEOMUTT_SRC_DIR:-$HOME/src/neomutt}"
OPENSSL4_PATCH_URL="${OPENSSL4_PATCH_URL:-https://github.com/neomutt/neomutt/commit/6a2f636ebed03d8d4a579ec0ff541ed6c2c90d8f.patch}"
# Set NEOMUTT_WITH_DOCS=1 to also build the HTML manual (needs docbook).
WITH_DOCS="${NEOMUTT_WITH_DOCS:-0}"

echo "Refreshing the package database and installing MSYS2 build dependencies..."
pacman -Sy --noconfirm
pacman -S --needed --noconfirm \
  $(pacman -Sgq base-devel) \
  git \
  pkgconf \
  ncurses-devel \
  libiconv-devel \
  openssl-devel \
  libsasl-devel \
  libgdbm-devel \
  libidn2-devel \
  gettext-devel \
  libgpgme-devel \
  heimdal-devel

if [ "$WITH_DOCS" = "1" ]; then
  pacman -S --needed --noconfirm libxslt docbook-xsl docbook-xml
fi

mkdir -p "$(dirname "$SRC_DIR")"
if [ ! -d "$SRC_DIR/.git" ]; then
  git clone https://github.com/neomutt/neomutt.git "$SRC_DIR"
fi

cd "$SRC_DIR"
git fetch --tags origin
if git rev-parse -q --verify "refs/tags/${REF}^{}" >/dev/null; then
  git checkout -f "tags/${REF}"
elif git rev-parse -q --verify "$REF" >/dev/null; then
  git checkout -f "$REF"
else
  git fetch --depth 1 origin "refs/tags/${REF}:refs/tags/${REF}" || git fetch --depth 1 origin "$REF"
  git checkout -f "$REF"
fi

# Tag 20260616 predates OpenSSL 4 support. Current MSYS2 ships OpenSSL 4.
# Main after 6a2f636 (2026-10-15) already contains the change.
if ! grep -q ASN1_STRING_get0_data conn/openssl.c; then
  echo "Applying OpenSSL 4 compatibility patch..."
  curl -fsSL -o /tmp/neomutt-openssl4.patch "$OPENSSL4_PATCH_URL"
  patch -p1 --forward < /tmp/neomutt-openssl4.patch
else
  echo "OpenSSL 4 compatibility is already in this tree."
fi

DOC_FLAG="--disable-doc"
if [ "$WITH_DOCS" = "1" ]; then
  DOC_FLAG="--doc"
fi

# --with-ui=ncurses matches the MSYS2 package. Autosetup still accepts it on
# the 20260616 tag. Newer main marks it deprecated and ignores it.
./configure \
  --prefix=/usr \
  --sysconfdir=/etc \
  --libexecdir=/usr/lib \
  --gpgme \
  --gss \
  --ssl \
  --sasl \
  --gdbm \
  --with-ui=ncurses \
  --with-idn2=/usr \
  --idn=0 \
  --idn2=1 \
  $DOC_FLAG

make -j"$(nproc)" EXTRA_CFLAGS="-Wno-incompatible-pointer-types"
echo "Build finished. Version string:"
./neomutt.exe -v | head -n 20

echo "Installing to /usr (C:\\msys64\\usr on a default MSYS2 install)..."
make install

echo
echo "Installed: $(command -v neomutt)"
echo "From PowerShell, add C:\\msys64\\usr\\bin to Path and run: neomutt -v"
echo "System line should start with MSYS_NT, which means the MSYS runtime is in use."
