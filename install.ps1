$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

Write-Host "==> Menyiapkan Dasai Mochi (Windows 10/11) di $RootDir"

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "==> Memasang dependensi Python dengan uv..."
    uv sync
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "==> Membuat virtualenv (.venv) dan memasang requirements.txt..."
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
    }
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m pip install --no-deps -e .
} else {
    Write-Error "Python 3.10+ tidak ditemukan. Pasang dari https://python.org atau 'winget install Python.Python.3.12'"
    exit 1
}

Write-Host ""
Write-Host "==> Status utilitas opsional:"
if (Get-Command mpv -ErrorAction SilentlyContinue) {
    Write-Host "  [OK] mpv terpasang (mendukung streaming & efek chibi speed/formant)"
} else {
    Write-Host "  [INFO] mpv belum ada - audio tetap jalan lewat Windows Media (winmm)."
    Write-Host "         Untuk efek suara chibi/anime lebih cepat: winget install mpv.net (atau scoop install mpv)"
}

if (Get-Command opencode -ErrorAction SilentlyContinue) {
    Write-Host "  [OK] opencode CLI terpasang"
} else {
    Write-Host "  [!] opencode CLI belum ditemukan di PATH (dibutuhkan untuk fitur chat AI: https://opencode.ai)"
}

Write-Host ""
Write-Host "Selesai! Jalankan di Windows Terminal / PowerShell dengan:"
Write-Host "  python mochi.py"
