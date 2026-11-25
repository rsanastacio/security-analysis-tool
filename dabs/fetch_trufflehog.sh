#!/bin/bash
# Download TruffleHog release tarballs on the installer machine so the secrets scanner
# does not need GitHub egress from the cluster. Output: ../lib/trufflehog/ (synced by the bundle).
# docs: https://github.com/trufflesecurity/trufflehog/releases
set -euo pipefail

TRUFFLEHOG_VERSION="${TRUFFLEHOG_VERSION:-3.94.3}"  # keep in sync with notebooks/Includes/scan_secrets/*.py
dest="$(cd "$(dirname "$0")/.." && pwd)/lib/trufflehog"
base="https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}"
mkdir -p "$dest"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

curl -fsSL "${base}/trufflehog_${TRUFFLEHOG_VERSION}_checksums.txt" -o "$dest/checksums.txt"
for arch in amd64 arm64; do
  file="trufflehog_${TRUFFLEHOG_VERSION}_linux_${arch}.tar.gz"
  expected=$(grep " ${file}\$" "$dest/checksums.txt" | cut -d' ' -f1)
  if [ -f "$dest/$file" ] && [ "$(sha256 "$dest/$file")" = "$expected" ]; then
    echo "TruffleHog ${arch}: already present"; continue
  fi
  curl -fsSL "${base}/${file}" -o "$dest/$file"
  if [ "$(sha256 "$dest/$file")" != "$expected" ]; then
    rm -f "$dest/$file"; echo "TruffleHog ${arch}: checksum mismatch"; exit 1
  fi
  echo "TruffleHog ${arch}: downloaded and verified"
done
