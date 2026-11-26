"""Cross-platform (Linux & Windows 10/11) safe computer control actions for Dasai Mochi."""

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


_ACTION_TAG_RE = re.compile(r"\[\s*action\s*:\s*([a-zA-Z0-9_-]+)(?:\s*:\s*([^\]]+))?\s*\]", re.IGNORECASE)
_SAFE_APP_TOKEN_RE = re.compile(r"^[a-zA-Z0-9._ -]{1,48}$")


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    arg: str = ""


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    kind: str
    message: str


APP_ALIASES_LINUX: dict[str, tuple[str, ...]] = {
    "browser": ("xdg-open", "firefox", "google-chrome", "chromium", "brave"),
    "firefox": ("firefox",),
    "chrome": ("google-chrome", "chromium", "chromium-browser"),
    "edge": ("microsoft-edge",),
    "terminal": ("x-terminal-emulator", "kitty", "alacritty", "wezterm", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"),
    "files": ("xdg-open", "nautilus", "dolphin", "thunar", "nemo", "pcmanfm"),
    "explorer": ("xdg-open", "nautilus", "dolphin", "thunar"),
    "calculator": ("gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "mate-calc", "xcalc"),
    "calc": ("gnome-calculator", "kcalc", "galculator", "qalculate-gtk", "xcalc"),
    "notepad": ("gedit", "mousepad", "kate", "xed", "leafpad", "code"),
    "editor": ("code", "gedit", "mousepad", "kate"),
    "vscode": ("code",),
    "code": ("code",),
    "settings": ("gnome-control-center", "systemsettings", "xfce4-settings-manager"),
    "spotify": ("spotify",),
    "discord": ("discord",),
    "vlc": ("vlc",),
}

APP_ALIASES_WINDOWS: dict[str, tuple[str, ...]] = {
    "browser": ("msedge.exe", "chrome.exe", "firefox.exe"),
    "firefox": ("firefox.exe",),
    "chrome": ("chrome.exe",),
    "edge": ("msedge.exe",),
    "terminal": ("wt.exe", "powershell.exe", "cmd.exe"),
    "powershell": ("powershell.exe",),
    "cmd": ("cmd.exe",),
    "files": ("explorer.exe",),
    "explorer": ("explorer.exe",),
    "calculator": ("calc.exe",),
    "calc": ("calc.exe",),
    "notepad": ("notepad.exe",),
    "editor": ("notepad.exe", "code.cmd", "code"),
    "vscode": ("code.cmd", "code"),
    "code": ("code.cmd", "code"),
    "settings": ("ms-settings:",),
    "paint": ("mspaint.exe",),
    "taskmgr": ("taskmgr.exe",),
    "spotify": ("spotify.exe",),
    "discord": ("discord.exe",),
    "vlc": ("vlc.exe",),
}

FOLDER_MAP: dict[str, str] = {
    "home": "",
    "downloads": "Downloads",
    "download": "Downloads",
    "unduhan": "Downloads",
    "documents": "Documents",
    "dokumen": "Documents",
    "desktop": "Desktop",
    "pictures": "Pictures",
    "gambar": "Pictures",
    "music": "Music",
    "musik": "Music",
    "videos": "Videos",
    "video": "Videos",
}


def extract_actions_and_text(raw_text: str) -> tuple[list[ActionSpec], str]:
    """Extract `[action:kind]` or `[action:kind:arg]` tags and strip them from `raw_text`."""
    actions: list[ActionSpec] = []
    for match in _ACTION_TAG_RE.finditer(raw_text):
        kind = match.group(1).strip().lower().replace("-", "_")
        arg = (match.group(2) or "").strip()
        actions.append(ActionSpec(kind=kind, arg=arg))
    cleaned = _ACTION_TAG_RE.sub("", raw_text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return actions, cleaned


def _launch_detached(cmd: list[str]) -> None:
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]


def _open_path_with_os(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = shutil.which("xdg-open")
    if not opener:
        raise RuntimeError("xdg-open tidak tersedia.")
    _launch_detached([opener, str(path)])


def _open_app(name: str) -> ActionResult:
    key = name.strip().lower()
    if not key or not _SAFE_APP_TOKEN_RE.match(key):
        return ActionResult(False, "open_app", f"Nama aplikasi tidak valid: '{name}'.")

    if os.name == "nt":
        candidates = APP_ALIASES_WINDOWS.get(key, (f"{key}.exe", key))
        for cand in candidates:
            if cand.endswith(":"):
                os.startfile(cand)  # type: ignore[attr-defined]
                return ActionResult(True, "open_app", f"Membuka {key}.")
            resolved = shutil.which(cand)
            if resolved:
                _launch_detached([resolved])
                return ActionResult(True, "open_app", f"Membuka {cand}.")
        try:
            os.startfile(candidates[0])  # type: ignore[attr-defined]
            return ActionResult(True, "open_app", f"Membuka {candidates[0]}.")
        except OSError as exc:
            return ActionResult(False, "open_app", f"Aplikasi '{name}' tidak ditemukan ({exc}).")

    candidates = APP_ALIASES_LINUX.get(key, (key,))
    for cand in candidates:
        resolved = shutil.which(cand)
        if not resolved:
            continue
        if cand == "xdg-open":
            if key in ("files", "explorer"):
                _launch_detached([resolved, str(Path.home())])
            else:
                _launch_detached([resolved, "https://www.google.com"])
        else:
            _launch_detached([resolved])
        return ActionResult(True, "open_app", f"Membuka {cand}.")
    return ActionResult(False, "open_app", f"Aplikasi '{name}' tidak ditemukan di PATH.")


def _open_url(url: str) -> ActionResult:
    target = url.strip()
    if not target:
        return ActionResult(False, "open_url", "URL kosong.")
    if not re.match(r"^https?://", target, re.IGNORECASE):
        if "." in target and " " not in target and ":" not in target:
            target = "https://" + target
        else:
            return ActionResult(False, "open_url", "Hanya URL http/https yang diizinkan.")
    webbrowser.open(target)
    return ActionResult(True, "open_url", f"Membuka {target}.")


def _search_web(query: str) -> ActionResult:
    q = query.strip()
    if not q:
        return ActionResult(False, "search_web", "Kata kunci pencarian kosong.")
    if q.lower().startswith("youtube "):
        term = q[8:].strip()
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(term)}"
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"
    webbrowser.open(url)
    return ActionResult(True, "search_web", f"Mencari: {q}")


def _open_folder(folder_name: str) -> ActionResult:
    key = (folder_name or "home").strip().lower()
    sub = FOLDER_MAP.get(key)
    if sub is None:
        return ActionResult(False, "open_folder", f"Folder '{folder_name}' tidak dikenal.")
    target = Path.home() / sub if sub else Path.home()
    if not target.exists():
        target = Path.home()
    _open_path_with_os(target)
    return ActionResult(True, "open_folder", f"Membuka folder {target}.")


def _windows_send_vk(vk_code: int) -> None:
    import ctypes

    keyeventf_keyup = 0x0002
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, keyeventf_keyup, 0)


def _control_volume(arg: str) -> ActionResult:
    cmd_arg = (arg or "").strip().lower()
    if os.name == "nt":
        vk_map = {"up": 0xAF, "naik": 0xAF, "down": 0xAE, "turun": 0xAE, "mute": 0xAD, "unmute": 0xAD}
        if cmd_arg in vk_map:
            repeats = 3 if cmd_arg in ("up", "naik", "down", "turun") else 1
            for _ in range(repeats):
                _windows_send_vk(vk_map[cmd_arg])
            return ActionResult(True, "volume", f"Volume {cmd_arg} dijalankan.")
        if cmd_arg.rstrip("%").isdigit():
            pct = max(0, min(100, int(cmd_arg.rstrip("%"))))
            for _ in range(50):
                _windows_send_vk(0xAE)
            for _ in range(round(pct / 2)):
                _windows_send_vk(0xAF)
            return ActionResult(True, "volume", f"Volume diatur sekitar {pct}%.")
        return ActionResult(False, "volume", f"Argumen volume tidak dikenal: '{arg}'.")

    if shutil.which("wpctl"):
        sink = "@DEFAULT_AUDIO_SINK@"
        if cmd_arg in ("up", "naik"):
            subprocess.run(["wpctl", "set-volume", "-l", "1.0", sink, "10%+"], check=False)
        elif cmd_arg in ("down", "turun"):
            subprocess.run(["wpctl", "set-volume", sink, "10%-"], check=False)
        elif cmd_arg in ("mute", "unmute", "toggle"):
            subprocess.run(["wpctl", "set-mute", sink, "toggle"], check=False)
        elif cmd_arg.rstrip("%").isdigit():
            pct = max(0, min(100, int(cmd_arg.rstrip("%"))))
            subprocess.run(["wpctl", "set-volume", sink, f"{pct}%"], check=False)
        else:
            return ActionResult(False, "volume", f"Argumen volume tidak dikenal: '{arg}'.")
        return ActionResult(True, "volume", f"Volume {cmd_arg} dijalankan (wpctl).")

    if shutil.which("pactl"):
        sink = "@DEFAULT_SINK@"
        if cmd_arg in ("up", "naik"):
            subprocess.run(["pactl", "set-sink-volume", sink, "+10%"], check=False)
        elif cmd_arg in ("down", "turun"):
            subprocess.run(["pactl", "set-sink-volume", sink, "-10%"], check=False)
        elif cmd_arg in ("mute", "unmute", "toggle"):
            subprocess.run(["pactl", "set-sink-mute", sink, "toggle"], check=False)
        elif cmd_arg.rstrip("%").isdigit():
            pct = max(0, min(100, int(cmd_arg.rstrip("%"))))
            subprocess.run(["pactl", "set-sink-volume", sink, f"{pct}%"], check=False)
        else:
            return ActionResult(False, "volume", f"Argumen volume tidak dikenal: '{arg}'.")
        return ActionResult(True, "volume", f"Volume {cmd_arg} dijalankan (pactl).")

    if shutil.which("amixer"):
        if cmd_arg in ("up", "naik"):
            subprocess.run(["amixer", "-q", "sset", "Master", "10%+"], check=False)
        elif cmd_arg in ("down", "turun"):
            subprocess.run(["amixer", "-q", "sset", "Master", "10%-"], check=False)
        elif cmd_arg in ("mute", "unmute", "toggle"):
            subprocess.run(["amixer", "-q", "sset", "Master", "toggle"], check=False)
        elif cmd_arg.rstrip("%").isdigit():
            pct = max(0, min(100, int(cmd_arg.rstrip("%"))))
            subprocess.run(["amixer", "-q", "sset", "Master", f"{pct}%"], check=False)
        else:
            return ActionResult(False, "volume", f"Argumen volume tidak dikenal: '{arg}'.")
        return ActionResult(True, "volume", f"Volume {cmd_arg} dijalankan (amixer).")

    return ActionResult(False, "volume", "Tool kontrol volume (wpctl/pactl/amixer) tidak ditemukan.")


def _control_media(arg: str) -> ActionResult:
    cmd_arg = (arg or "play_pause").strip().lower()
    if os.name == "nt":
        vk_map = {
            "play_pause": 0xB3,
            "play": 0xB3,
            "pause": 0xB3,
            "next": 0xB0,
            "prev": 0xB1,
            "previous": 0xB1,
            "stop": 0xB2,
        }
        vk = vk_map.get(cmd_arg)
        if vk is None:
            return ActionResult(False, "media", f"Perintah media tidak dikenal: '{arg}'.")
        _windows_send_vk(vk)
        return ActionResult(True, "media", f"Media {cmd_arg} dijalankan.")

    if not shutil.which("playerctl"):
        return ActionResult(False, "media", "Tool 'playerctl' belum terpasang di Linux.")
    p_map = {
        "play_pause": "play-pause",
        "play": "play",
        "pause": "pause",
        "next": "next",
        "prev": "previous",
        "previous": "previous",
        "stop": "stop",
    }
    sub = p_map.get(cmd_arg)
    if not sub:
        return ActionResult(False, "media", f"Perintah media tidak dikenal: '{arg}'.")
    subprocess.run(["playerctl", sub], check=False)
    return ActionResult(True, "media", f"Media {sub} dijalankan.")


def _take_screenshot() -> ActionResult:
    pictures = Path.home() / "Pictures"
    out_dir = pictures if pictures.exists() else Path.home()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"mochi_screenshot_{ts}.png"

    if os.name == "nt":
        ps_script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height);"
            "$g = [System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size);"
            f"$bmp.Save('{str(out_path)}');"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, check=False)
        if res.returncode == 0 and out_path.exists():
            return ActionResult(True, "screenshot", f"Screenshot disimpan ke {out_path}.")
        return ActionResult(False, "screenshot", "Gagal mengambil screenshot di Windows.")

    tools: list[list[str]] = []
    if shutil.which("grim"):
        tools.append(["grim", str(out_path)])
    if shutil.which("gnome-screenshot"):
        tools.append(["gnome-screenshot", "-f", str(out_path)])
    if shutil.which("spectacle"):
        tools.append(["spectacle", "-b", "-n", "-o", str(out_path)])
    if shutil.which("scrot"):
        tools.append(["scrot", str(out_path)])
    if shutil.which("import"):
        tools.append(["import", "-window", "root", str(out_path)])

    for cmd in tools:
        res = subprocess.run(cmd, capture_output=True, check=False)
        if res.returncode == 0 and out_path.exists():
            return ActionResult(True, "screenshot", f"Screenshot disimpan ke {out_path}.")
    return ActionResult(False, "screenshot", "Tool screenshot (grim/gnome-screenshot/spectacle/scrot) tidak ditemukan.")


def get_system_summary() -> str:
    """Collect a compact cross-platform CPU/RAM/disk/battery/time summary."""
    now_str = datetime.datetime.now().strftime("%H:%M, %d %b %Y")
    cpu_cnt = os.cpu_count() or 1
    disk = shutil.disk_usage(Path.home())
    disk_free_gb = disk.free / (1024**3)

    ram_info = ""
    bat_info = ""
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                avail_gb = stat.ullAvailPhys / (1024**3)
                total_gb = stat.ullTotalPhys / (1024**3)
                ram_info = f"RAM bebas {avail_gb:.1f}/{total_gb:.1f} GB"
        except Exception:
            pass
    else:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            vals: dict[str, int] = {}
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    vals[parts[0].rstrip(":")] = int(parts[1])
            if "MemTotal" in vals and "MemAvailable" in vals:
                avail_gb = vals["MemAvailable"] / (1024**2)
                total_gb = vals["MemTotal"] / (1024**2)
                ram_info = f"RAM bebas {avail_gb:.1f}/{total_gb:.1f} GB"

        for bat_dir in sorted(Path("/sys/class/power_supply").glob("BAT*")):
            cap_file = bat_dir / "capacity"
            st_file = bat_dir / "status"
            if cap_file.exists():
                cap = cap_file.read_text(encoding="utf-8", errors="ignore").strip()
                st = st_file.read_text(encoding="utf-8", errors="ignore").strip() if st_file.exists() else ""
                bat_info = f"Baterai {cap}% ({st})" if st else f"Baterai {cap}%"
                break

    parts = [f"Jam {now_str}", f"CPU {cpu_cnt} thread"]
    if ram_info:
        parts.append(ram_info)
    parts.append(f"Disk sisa {disk_free_gb:.1f} GB")
    if bat_info:
        parts.append(bat_info)
    return ", ".join(parts)


def _lock_screen() -> ActionResult:
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.LockWorkStation()
        return ActionResult(True, "lock_screen", "Layar Windows dikunci.")

    for tool, cmd in (
        ("loginctl", ["loginctl", "lock-session"]),
        ("xdg-screensaver", ["xdg-screensaver", "lock"]),
        ("hyprlock", ["hyprlock"]),
        ("swaylock", ["swaylock"]),
        ("dm-tool", ["dm-tool", "lock"]),
    ):
        if shutil.which(tool):
            _launch_detached(cmd)
            return ActionResult(True, "lock_screen", f"Mengunci layar ({tool}).")
    return ActionResult(False, "lock_screen", "Tool pengunci layar tidak ditemukan.")


def _copy_clipboard(text: str) -> ActionResult:
    if not text:
        return ActionResult(False, "clipboard", "Teks clipboard kosong.")
    if os.name == "nt":
        subprocess.run(["clip"], input=text, text=True, encoding="utf-8", check=False)
        return ActionResult(True, "clipboard", "Teks disalin ke clipboard.")
    for tool, cmd in (
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
    ):
        if shutil.which(tool):
            subprocess.run(cmd, input=text, text=True, encoding="utf-8", check=False)
            return ActionResult(True, "clipboard", f"Teks disalin via {tool}.")
    return ActionResult(False, "clipboard", "Tool clipboard (wl-copy/xclip/xsel) tidak tersedia.")


def _parse_duration_seconds(arg: str) -> int:
    raw = arg.strip().lower()
    if raw.isdigit():
        return max(1, min(3600, int(raw)))
    match = re.match(r"^(\d+)\s*(s|sec|detik|m|min|menit|h|jam)$", raw)
    if not match:
        return 0
    val = int(match.group(1))
    unit = match.group(2)
    if unit in ("m", "min", "menit"):
        val *= 60
    elif unit in ("h", "jam"):
        val *= 3600
    return max(1, min(3600, val))


def execute_action(
    spec: ActionSpec,
    on_timer_done: Callable[[str], None] | None = None,
) -> ActionResult:
    """Execute one structured cross-platform action."""
    kind = spec.kind
    arg = spec.arg

    try:
        if kind == "open_app":
            return _open_app(arg)
        if kind == "open_url":
            return _open_url(arg)
        if kind == "search_web":
            return _search_web(arg)
        if kind == "open_folder":
            return _open_folder(arg)
        if kind == "volume":
            return _control_volume(arg)
        if kind == "media":
            return _control_media(arg)
        if kind == "screenshot":
            return _take_screenshot()
        if kind == "sysinfo":
            return ActionResult(True, "sysinfo", get_system_summary())
        if kind == "lock_screen":
            return _lock_screen()
        if kind == "clipboard":
            return _copy_clipboard(arg)
        if kind == "timer":
            secs = _parse_duration_seconds(arg)
            if secs <= 0:
                return ActionResult(False, "timer", f"Durasi timer tidak valid: '{arg}'.")

            def _notify() -> None:
                if on_timer_done is not None:
                    on_timer_done(f"Waktu timer {secs} detik sudah selesai!")

            timer = threading.Timer(float(secs), _notify)
            timer.daemon = True
            timer.start()
            return ActionResult(True, "timer", f"Timer {secs} detik dimulai.")

        return ActionResult(False, kind, f"Aksi '{kind}' tidak dikenal.")
    except Exception as exc:
        return ActionResult(False, kind, f"Gagal menjalankan {kind}: {exc}")
