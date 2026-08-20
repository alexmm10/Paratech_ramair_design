#!/usr/bin/env bash
set -euo pipefail

VERSION="4.15.2"
ARCHIVE="gmsh-${VERSION}-Linux64.tgz"
URL="https://gmsh.info/bin/Linux/${ARCHIVE}"
INSTALL_ROOT="${HOME}/.local/opt"
INSTALL_DIR="${INSTALL_ROOT}/gmsh-${VERSION}"
BIN_DIR="${HOME}/.local/bin"
TMP_DIR="$(mktemp -d -t ramair-gmsh-install-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT

mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}"

if [[ ! -x "${INSTALL_DIR}/bin/gmsh" ]]; then
  echo "Downloading Gmsh ${VERSION} from ${URL}"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --output "${TMP_DIR}/${ARCHIVE}" "${URL}"
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --output-document="${TMP_DIR}/${ARCHIVE}" "${URL}"
  else
    echo "ERROR: curl or wget is required." >&2
    exit 2
  fi
  tar -xzf "${TMP_DIR}/${ARCHIVE}" -C "${TMP_DIR}"
  if [[ -e "${INSTALL_DIR}" ]]; then
    mv "${INSTALL_DIR}" "${INSTALL_DIR}.previous.$(date +%Y%m%d_%H%M%S)"
  fi
  mv "${TMP_DIR}/gmsh-${VERSION}-Linux64" "${INSTALL_DIR}"
fi

ln -sfn "${INSTALL_DIR}/bin/gmsh" "${BIN_DIR}/gmsh"
export PATH="${BIN_DIR}:${PATH}"
export RAMAIR_GMSH_EXECUTABLE="${INSTALL_DIR}/bin/gmsh"

echo "Installed: ${RAMAIR_GMSH_EXECUTABLE}"
"${RAMAIR_GMSH_EXECUTABLE}" --version
echo
echo "Add these lines to ~/.bashrc if they are not already present:"
echo 'export PATH="$HOME/.local/bin:$PATH"'
echo 'export RAMAIR_GMSH_EXECUTABLE="$HOME/.local/opt/gmsh-4.15.2/bin/gmsh"'
