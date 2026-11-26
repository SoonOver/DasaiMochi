# Dasai Mochi (Terminal Edition)

**Dasai Mochi** (*digital car/desk companion*) versi terminal berbasis Python (`curses` + 64×32 virtual OLED *framebuffer*), lengkap dengan animasi ekspresi wajah interaktif, **input mikrofon (STT)**, **suara imut/anime (TTS)**, **subtitle streaming**, **kendali komputer (Linux & Windows 10/11)**, serta **chat AI gratis tanpa wajib download OpenCode**.

## Fitur Utama

- **10 Ekspresi OLED Animatif** (`idle`, `happy`, `love`, `sleepy`, `cool`, `surprised`, `angry`, `vibe`, `turbo`, `glitch`) dengan kedipan alami, lirikan mata, dan reaksi *boop* (`Space`).
- **Tampilan Minimalis**: Menu dan legenda tersembunyi secara default — tekan `h` kapan saja untuk membuka menu lengkap.
- **Tanpa Wajib Download OpenCode**: Jika `opencode` CLI tidak terpasang, Mochi otomatis memakai API AI gratis tanpa API key (`Pollinations` / `OpenCode Zen HTTP`), dan tetap memakai `opencode/beam-free` bila `opencode` tersedia.
- **Kendali Komputer Lintas OS (Linux & Windows 10/11)**: Minta Mochi membuka aplikasi (`browser`, `calculator`, `notepad`, `vscode`, `files`), mencari di Google/YouTube, membuka folder, mengatur volume & media playback, mengambil screenshot, membaca CPU/RAM/baterai/jam, mengunci layar, menyalin clipboard, atau memasang timer.
- **TTS Ringan & Cepat + Subtitle Streaming**:
  - Default **`tiktok-id`** (`id_001` dengan *formant shift* chibi via `mpv` atau Windows Media `winmm`)
  - Karakter anime **`zunda-jp`** / **`zunda-norm`** (VOICEVOX Zundamon), **`tiktok-anime`**, **`tiktok-jp`**, **`tiktok-en`**, **`gtts-chibi`**, serta opsi **`edge-chibi`** / **`edge-cute`**
  - Subtitle muncul mengalir (*streaming*) tepat di bawah layar Mochi.

## Prasyarat Sistem

- **Python 3.10+** (`3.11`–`3.14` diuji di CI)
- **Linux**:
  - Audio playback: `mpv` (direkomendasikan untuk streaming & efek pitch/formant *chibi*), atau `ffplay` / `pw-play` / `paplay` / `aplay`
  - Mic capture *(opsional)*: `pw-record` (PipeWire), `parecord` (PulseAudio), `arecord` (ALSA), atau `ffmpeg`
- **Windows 10 / Windows 11**:
  - Jalankan di **Windows Terminal** (atau PowerShell) agar karakter OLED & warna tampil optimal
  - Audio playback & mic capture sudah jalan langsung lewat Windows Media (`winmm`) & `sounddevice` (pasang `mpv` opsional via `winget install mpv.net` untuk efek suara *chibi* lebih cepat)
- **OpenCode CLI** *(opsional)*: jika [`opencode`](https://opencode.ai) terpasang di `PATH`, Mochi memakainya otomatis; jika tidak, Mochi tetap jalan pakai backend HTTP gratis bawaan (`--backend pollinations`).

## Instalasi (Fresh Clone)

### Linux / macOS (Otomatis 1 Perintah)

```bash
git clone https://github.com/SoonOver/DasaiMochi.git
cd DasaiMochi
./install.sh
./mochi.py
```

### Windows 10 / Windows 11 (Otomatis)

Klik dua kali **`install.bat`** atau jalankan lewat PowerShell / Windows Terminal:

```powershell
git clone https://github.com/SoonOver/DasaiMochi.git
cd DasaiMochi
.\install.bat
python mochi.py
```

### Manual dengan `pip` + `requirements.txt` (Linux & Windows)

```bash
git clone https://github.com/SoonOver/DasaiMochi.git
cd DasaiMochi
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows    : .venv\Scripts\activate
pip install -r requirements.txt
python mochi.py
```

### Menggunakan `uv`

```bash
git clone https://github.com/SoonOver/DasaiMochi.git
cd DasaiMochi
uv sync
uv run dasai-mochi
```

Atau jalankan langsung lewat *launcher* ([mochi.py](mochi.py) otomatis memakai `.venv` lokal jika ada):

```bash
python3 mochi.py
# atau:
./mochi.py
```

## Contoh Penggunaan CLI

```bash
# Langsung mendengarkan mic saat dibuka
./mochi.py --talk

# Pakai suara karakter anime Zundamon (Jepang) atau TikTok Anime
./mochi.py --voice zunda-jp
./mochi.py --voice tiktok-anime

# Pakai model gratis OpenCode lain & atur speed/formant shift manual
./mochi.py --model opencode/hy4-flash-free --voice tiktok-id --speed 1.22

# Tanya satu kalimat lewat CLI (cetak + bicara lewat TTS)
./mochi.py --ask "halo mochi, apa kabar?"

# Cetak 1 frame statis ke stdout (tanpa TUI)
./mochi.py --snapshot --mood love

# Lihat daftar mood, model gratis, dan preset suara
./mochi.py --list-moods
./mochi.py --list-models
./mochi.py --list-voices
```

## Kontrol Keyboard (Saat Berjalan)

| Tombol | Aksi |
| --- | --- |
| `v` / `r` | Mulai / hentikan rekam suara dari **mic** (otomatis berhenti saat jeda bicara) |
| `t` | Ketik pesan teks langsung ke OpenCode + TTS |
| `o` | Ganti model gratis OpenCode (`beam-free`, `hy4-flash-free`, `deepseek-v4.1-flash-free`, `qwen4-max-free`, `big-pickle`, dll.) |
| `g` | Ganti suara & bahasa TTS/STT (`TikTok Mochi ID`, `Zundamon Sweet`, `TikTok Anime JP`, dll.) |
| `s` | Toggle Mute / Unmute suara TTS |
| `Space` | **Boop / Pat** Mochi |
| `1`–`9`, `0` | Pilih mood langsung |
| `←` / `→` atau `p` / `n` | Ganti mood sebelumnya / berikutnya |
| `c` | Ganti tema warna OLED (`cyan`, `white`, `amber`, `green`, `pink`, `red`) |
| `a` | Toggle mode `AUTO` / `LOCK` |
| `b` | Toggle bingkai helm (*bezel*) |
| `m` | Toggle mode tampilan `OLED` (Unicode half-block) / `ASCII` |
| `h` / `?` | Buka / tutup menu & legenda kontrol (tersembunyi secara default) |
| `q` / `Esc` | Keluar |

## Pengembangan & Kontribusi

```bash
uv sync --all-extras
uv run pytest -v
```

Pull request dan laporan bug sangat diterima! Untuk isu keamanan, lihat [SECURITY.md](SECURITY.md).

## Lisensi

Proyek ini dilisensikan di bawah [Apache License 2.0](LICENSE).
