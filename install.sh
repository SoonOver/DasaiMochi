#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Menyiapkan Dasai Mochi di $ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 tidak ditemukan. Pasang Python 3.10+ terlebih dahulu." >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    echo "==> Memasang dependensi Python dengan uv..."
    uv sync
else
    echo "==> Membuat virtualenv (.venv) dan memasang requirements.txt..."
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pip install --no-deps -e .
fi

chmod +x mochi.py

echo
echo "==> Mengecek utilitas sistem opsional (audio & AI):"
missing=0

if command -v mpv >/dev/null 2>&1; then
    echo "  [OK] mpv (audio playback & chibi speed/formant shift)"
elif command -v ffplay >/dev/null 2>&1 || command -v pw-play >/dev/null 2>&1 || command -v aplay >/dev/null 2>&1; then
    echo "  [OK] audio player tersedia (disarankan pasang 'mpv' untuk hasil TTS terbaik)"
else
    echo "  [!] Audio player belum ada (pasang: mpv)"
    missing=1
fi

if command -v pw-record >/dev/null 2>&1 || command -v parecord >/dev/null 2>&1 || command -v arecord >/dev/null 2>&1 || command -v ffmpeg >/dev/null 2>&1; then
    echo "  [OK] perekam mic tersedia"
else
    echo "  [!] Perekam mic belum ada (pasang: pipewire/pulseaudio/alsa-utils atau ffmpeg)"
    missing=1
fi

if command -v opencode >/dev/null 2>&1; then
    echo "  [OK] opencode CLI terpasang"
else
    echo "  [INFO] opencode CLI tidak ada - Mochi otomatis pakai API AI gratis bawaan tanpa download opencode."
fi

if [ "$missing" -eq 1 ]; then
    echo
    echo "Tips pasang paket sistem (sesuaikan distro):"
    echo "  Arch Linux   : sudo pacman -S mpv alsa-utils"
    echo "  Ubuntu/Debian: sudo apt install mpv alsa-utils"
    echo "  Fedora       : sudo dnf install mpv alsa-utils"
fi

echo
echo "Selesai! Jalankan dengan:"
echo "  ./mochi.py"
