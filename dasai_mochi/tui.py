"""Interactive curses TUI for Dasai Mochi with voice chat, barge-in, hands-free loop, and PC control."""

from __future__ import annotations

import contextlib
import curses
import os
import signal
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass, field

from .animations import MOOD_BY_KEY, MOODS, MochiEngine
from .audio import (
    DEFAULT_VOICE_KEY,
    AudioError,
    VoicePreset,
    next_voice_preset,
    record_mic_to_wav,
    resolve_voice,
    speak_text,
    transcribe_wav,
)
from .control import execute_action, get_battery_percent
from .framebuffer import Framebuffer, build_bezel
from .llm import (
    DEFAULT_FREE_MODEL,
    KILO_MODELS,
    OpenCodeClient,
    OpenCodeError,
    discover_free_models,
    models_for_backend,
)
from .themes import Theme, get_theme, next_theme

# Color pair IDs
PAIR_OLED = 1
PAIR_OLED_DIM = 2
PAIR_BEZEL = 3
PAIR_HUD = 4
PAIR_HUD_ACCENT = 5
PAIR_WARN = 6

_STOP_PHRASES = {"stop", "berhenti", "cukup", "diam", "selesai", "matikan handsfree", "stop listening"}


def _init_colors(theme_name: str) -> Theme:
    theme = get_theme(theme_name)
    if not curses.has_colors():
        return theme

    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK

    use_256 = curses.COLORS >= 256
    fg = theme.curses_256_fg if use_256 else theme.curses_basic_fg
    dim = theme.curses_256_dim if use_256 else theme.curses_basic_fg
    bezel = 242 if use_256 else curses.COLOR_WHITE
    hud = 248 if use_256 else curses.COLOR_WHITE
    warn = 203 if use_256 else curses.COLOR_RED

    curses.init_pair(PAIR_OLED, fg, curses.COLOR_BLACK)
    curses.init_pair(PAIR_OLED_DIM, dim, curses.COLOR_BLACK)
    curses.init_pair(PAIR_BEZEL, bezel, bg)
    curses.init_pair(PAIR_HUD, hud, bg)
    curses.init_pair(PAIR_HUD_ACCENT, fg, bg)
    curses.init_pair(PAIR_WARN, warn, bg)
    return theme


def _safe_addstr(win: curses.window, y: int, x: int, text: str, attr: int = 0) -> None:
    max_y, max_x = win.getmaxyx()
    if y < 0 or y >= max_y or x >= max_x:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    avail = max_x - x - (1 if y == max_y - 1 else 0)
    if avail <= 0:
        return
    with contextlib.suppress(curses.error):
        win.addstr(y, x, text[:avail], attr)


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 3 or len(text) <= limit:
        return text[: max(0, limit)]
    return text[: limit - 1] + "…"


@dataclass
class VoiceController:
    """Thread-safe coordinator between the 24 FPS TUI and voice/AI tasks."""

    engine: MochiEngine
    llm: OpenCodeClient
    voice: VoicePreset
    tts_enabled: bool = True
    hands_free: bool = False
    models: tuple[str, ...] = KILO_MODELS

    lock: threading.Lock = field(default_factory=threading.Lock)
    voice_state: str = "none"
    mic_level: float = 0.0
    status_line: str = ""
    last_user: str = ""
    last_mochi: str = ""
    is_error: bool = False

    _busy: bool = False
    _rec_stop_event: threading.Event | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _app_closed: threading.Event = field(default_factory=threading.Event)
    _worker_done: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self._worker_done.set()

    def refresh_models_async(self) -> None:
        def _worker() -> None:
            found = discover_free_models(backend=self.llm.active_backend())
            with self.lock:
                self.models = found
                if found and self.llm.model not in found:
                    self.llm.model = found[0]

        threading.Thread(target=_worker, daemon=True).start()

    def is_listening(self) -> bool:
        with self.lock:
            return self.voice_state == "listening"

    def is_busy(self) -> bool:
        with self.lock:
            return self._busy

    def stop_recording(self) -> None:
        with self.lock:
            if self._rec_stop_event is not None:
                self._rec_stop_event.set()
                self.status_line = "Memproses suara..."

    def interrupt_speaking(self, wait_timeout: float = 0.5) -> bool:
        """Stop active TTS/turn cleanly so the user can barge in immediately."""
        with self.lock:
            if not self._busy:
                return True
            if self.voice_state != "speaking":
                return False
            self._cancel_event.set()
            if self._rec_stop_event is not None:
                self._rec_stop_event.set()
            done_ev = self._worker_done

        finished = done_ev.wait(timeout=wait_timeout)
        with self.lock:
            if finished or not self._busy:
                self.voice_state = "none"
                self._busy = False
                return True
        return False

    def stop_current_turn(self) -> None:
        """Stop active recording, thinking, speaking, or hands-free loop without quitting."""
        with self.lock:
            self.hands_free = False
            self._cancel_event.set()
            if self._rec_stop_event is not None:
                self._rec_stop_event.set()
            self.voice_state = "none"
            self.mic_level = 0.0
            self.status_line = "Dihentikan."
            self.is_error = False

    def cancel_all(self) -> None:
        self._app_closed.set()
        with self.lock:
            self.hands_free = False
            self._cancel_event.set()
            if self._rec_stop_event is not None:
                self._rec_stop_event.set()
            self.voice_state = "none"
            self.mic_level = 0.0
            self._busy = False

    def toggle_tts(self) -> bool:
        with self.lock:
            self.tts_enabled = not self.tts_enabled
            if not self.tts_enabled:
                self._cancel_event.set()
                if self.voice_state == "speaking":
                    self.voice_state = "none"
            self.status_line = f"TTS {'AKTIF' if self.tts_enabled else 'MUTE'}"
            self.is_error = False
            return self.tts_enabled

    def toggle_hands_free(self) -> bool:
        with self.lock:
            self.hands_free = not self.hands_free
            enabled = self.hands_free
            self.status_line = f"Hands-Free {'AKTIF' if enabled else 'MATI'}"
            self.is_error = False
            busy = self._busy
        if enabled and not busy:
            self.start_voice_turn()
        return enabled

    def cycle_model(self) -> str:
        with self.lock:
            backend = self.llm.active_backend()
            models = self.models or models_for_backend(backend)
            idx = models.index(self.llm.model) if self.llm.model in models else -1
            nxt = models[(idx + 1) % len(models)]
            self.llm.set_model(nxt)
            self.status_line = f"Model ({backend}): {nxt} (sesi baru)"
            self.is_error = False
            return nxt

    def cycle_voice(self) -> VoicePreset:
        with self.lock:
            old_lang = self.voice.lang
            self.voice = next_voice_preset(self.voice)
            self.llm.lang = self.voice.lang
            if self.voice.lang != old_lang:
                self.llm.reset_session()
            self.status_line = f"Suara TTS/STT: {self.voice.label} ({self.voice.lang})"
            self.is_error = False
            return self.voice

    def sync_to_engine(self) -> None:
        with self.lock:
            self.engine.voice_state = self.voice_state
            self.engine.mic_level = self.mic_level

    def _schedule_timer_alert(self, msg: str) -> None:
        def _wait_and_speak() -> None:
            while not self._app_closed.is_set():
                with self.lock:
                    if not self._busy:
                        self._busy = True
                        self._cancel_event = threading.Event()
                        cancel_ev = self._cancel_event
                        self._worker_done = threading.Event()
                        done_ev = self._worker_done
                        self.engine.set_mood("surprised")
                        self.last_mochi = msg
                        self.is_error = False
                        tts_flag = self.tts_enabled
                        v = self.voice
                        if tts_flag:
                            self.voice_state = "speaking"
                            self.status_line = "Timer selesai!"
                        break
                time.sleep(0.2)
            else:
                return

            try:
                if tts_flag and not cancel_ev.is_set():
                    speak_text(
                        msg,
                        voice=v,
                        on_end=lambda: self._finish_speaking_ui(msg),
                        display_text=msg,
                        cancel_event=cancel_ev,
                    )
            finally:
                with self.lock:
                    if self.voice_state == "speaking":
                        self.voice_state = "none"
                    self._busy = False
                done_ev.set()

        threading.Thread(target=_wait_and_speak, daemon=True).start()

    def _finish_speaking_ui(self, full_text: str) -> None:
        with self.lock:
            if self.voice_state == "speaking":
                self.voice_state = "none"
            if not self.last_mochi and not self.is_error:
                self.last_mochi = full_text
            self.status_line = ""

    def _run_llm_and_tts(self, user_text: str, cancel_ev: threading.Event) -> None:
        with self.lock:
            self.voice_state = "thinking"
            self.mic_level = 0.0
            self.last_user = user_text
            self.last_mochi = ""
            self.is_error = False
            active_b = self.llm.active_backend()
            allowed = models_for_backend(active_b)
            shown_model = (
                self.llm.model
                if (self.llm.model in allowed or ":" in self.llm.model or "/" in self.llm.model)
                else allowed[0]
            )
            model_short = shown_model.split("/")[-1]
            self.status_line = f"Mochi menjawab ({model_short})..."

        def _on_partial(partial_text: str, partial_mood: str | None) -> None:
            if cancel_ev.is_set():
                return
            with self.lock:
                if partial_mood:
                    self.engine.set_mood(partial_mood)
                self.last_mochi = partial_text

        reply = self.llm.ask(user_text, on_partial=_on_partial)
        if cancel_ev.is_set():
            return

        action_notes: list[str] = []
        if reply.actions:
            for spec in reply.actions:
                res = execute_action(spec, on_timer_done=self._schedule_timer_alert)
                if res.kind in ("sysinfo", "read_clipboard", "forget_memory") or not res.ok:
                    action_notes.append(res.message)

        final_text = reply.text
        final_spoken = reply.spoken_text
        if action_notes:
            note_str = " ".join(action_notes)
            final_text = f"{final_text} {note_str}".strip() if final_text else note_str
            final_spoken = f"{final_spoken}. {note_str}".strip() if final_spoken else note_str

        with self.lock:
            if reply.mood:
                self.engine.set_mood(reply.mood)
            self.is_error = False
            tts_on = self.tts_enabled
            voice = self.voice
            if tts_on:
                self.last_mochi = ""
            else:
                self.voice_state = "none"
                self.status_line = ""

        if tts_on and not cancel_ev.is_set():
            def _on_start() -> None:
                with self.lock:
                    if not cancel_ev.is_set():
                        self.voice_state = "speaking"
                        self.status_line = f"Mochi berbicara ({voice.label})..."

            def _on_word(revealed: str) -> None:
                if cancel_ev.is_set():
                    return
                with self.lock:
                    self.last_mochi = revealed

            speak_text(
                final_spoken,
                voice=voice,
                on_start=_on_start,
                on_word=_on_word,
                on_end=lambda: self._finish_speaking_ui(final_text),
                display_text=final_text,
                cancel_event=cancel_ev,
            )
        elif not cancel_ev.is_set():
            words = final_text.split()
            for idx in range(1, len(words) + 1):
                if cancel_ev.is_set():
                    return
                with self.lock:
                    self.last_mochi = " ".join(words[:idx])
                time.sleep(0.035)
            with self.lock:
                self.last_mochi = final_text

    def start_voice_turn(self) -> None:
        with self.lock:
            if self._busy:
                if self.voice_state == "listening" and self._rec_stop_event is not None:
                    self._rec_stop_event.set()
                    return
                if self.voice_state == "speaking":
                    pass
                else:
                    return
        if self.is_busy() and not self.interrupt_speaking():
            return

        with self.lock:
            if self._busy:
                return
            self._busy = True
            self._cancel_event = threading.Event()
            cancel_ev = self._cancel_event
            self._worker_done = threading.Event()
            done_ev = self._worker_done
            stop_ev = threading.Event()
            self._rec_stop_event = stop_ev
            self.voice_state = "listening"
            self.mic_level = 0.0
            self.is_error = False
            self.status_line = "Mendengarkan mic... bicara sekarang ('v' untuk kirim cepat)"
            lang = self.voice.lang

        def _worker() -> None:
            should_continue = False
            fd, wav_path = tempfile.mkstemp(prefix="mochi_mic_", suffix=".wav")
            os.close(fd)
            try:
                def _on_lvl(lvl: float) -> None:
                    with self.lock:
                        self.mic_level = lvl

                record_mic_to_wav(wav_path, stop_event=stop_ev, on_level=_on_lvl)
                if cancel_ev.is_set():
                    return

                with self.lock:
                    self.voice_state = "thinking"
                    self.mic_level = 0.0
                    self.status_line = "Mengubah suara ke teks (STT)..."

                transcript = transcribe_wav(wav_path, language=lang)
                if cancel_ev.is_set():
                    return

                if transcript.strip().lower() in _STOP_PHRASES:
                    with self.lock:
                        self.hands_free = False
                        self.last_user = transcript
                        self.last_mochi = "Oke, aku diam dulu. Tekan 'v' kalau butuh aku!"
                        self.voice_state = "none"
                        self.status_line = ""
                    return

                self._run_llm_and_tts(transcript, cancel_ev)
                with self.lock:
                    should_continue = self.hands_free and not cancel_ev.is_set() and not self._app_closed.is_set()
            except AudioError as exc:
                msg = str(exc)
                with self.lock:
                    self.voice_state = "none"
                    self.mic_level = 0.0
                    if self.hands_free and "tidak terdengar jelas" in msg.lower():
                        self.hands_free = False
                        self.is_error = False
                        self.status_line = "Tidak ada suara; hands-free dijeda ('f' untuk lanjut)"
                    else:
                        self.hands_free = False
                        self.is_error = True
                        self.status_line = msg
            except (OpenCodeError, ValueError) as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.mic_level = 0.0
                    self.hands_free = False
                    self.is_error = True
                    self.status_line = str(exc)
            except Exception as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.mic_level = 0.0
                    self.hands_free = False
                    self.is_error = True
                    self.status_line = f"Error tak terduga: {exc}"
            finally:
                with self.lock:
                    if self.voice_state in ("listening", "thinking", "speaking"):
                        self.voice_state = "none"
                    self.mic_level = 0.0
                    self._rec_stop_event = None
                    self._busy = False
                done_ev.set()
                if os.path.exists(wav_path):
                    with contextlib.suppress(OSError):
                        os.remove(wav_path)

            if should_continue:
                time.sleep(0.45)
                if not cancel_ev.is_set() and not self._app_closed.is_set():
                    self.start_voice_turn()

        threading.Thread(target=_worker, daemon=True).start()

    def start_text_turn(self, user_text: str) -> None:
        cleaned = user_text.strip()
        if not cleaned:
            return
        if self.is_busy() and not self.interrupt_speaking():
            with self.lock:
                self.status_line = "Tunggu proses sebelumnya selesai..."
            return

        with self.lock:
            if self._busy:
                self.status_line = "Tunggu proses sebelumnya selesai..."
                return
            self._busy = True
            self._cancel_event = threading.Event()
            cancel_ev = self._cancel_event
            self._worker_done = threading.Event()
            done_ev = self._worker_done

        def _worker() -> None:
            try:
                self._run_llm_and_tts(cleaned, cancel_ev)
            except (AudioError, OpenCodeError, ValueError) as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.is_error = True
                    self.status_line = str(exc)
            except Exception as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.is_error = True
                    self.status_line = f"Error tak terduga: {exc}"
            finally:
                with self.lock:
                    if self.voice_state in ("thinking", "speaking"):
                        self.voice_state = "none"
                    self._busy = False
                done_ev.set()

        threading.Thread(target=_worker, daemon=True).start()


def _prompt_text_input(stdscr: curses.window, max_len: int = 220) -> str | None:
    """Temporarily collect a line of text at the bottom of the terminal."""
    max_y, max_x = stdscr.getmaxyx()
    prompt_y = max(0, max_y - 1)
    prefix = "Chat > "
    buf: list[str] = []

    stdscr.nodelay(False)
    try:
        with contextlib.suppress(curses.error):
            curses.curs_set(1)

        while True:
            stdscr.move(prompt_y, 0)
            stdscr.clrtoeol()
            visible_avail = max(1, max_x - len(prefix) - 2)
            text_now = "".join(buf)
            tail = text_now[-visible_avail:]
            _safe_addstr(stdscr, prompt_y, 0, prefix + tail, curses.A_BOLD)
            stdscr.move(prompt_y, min(max_x - 1, len(prefix) + len(tail)))
            stdscr.refresh()

            try:
                ch = stdscr.get_wch()
            except curses.error:
                continue

            if ch in ("\n", "\r", 10, 13, curses.KEY_ENTER):
                return "".join(buf).strip() or None
            if ch in ("\x1b", 27):
                return None
            if ch in ("\x7f", "\b", 8, curses.KEY_BACKSPACE):
                if buf:
                    buf.pop()
            elif isinstance(ch, str) and ch.isprintable() and len(buf) < max_len:
                buf.append(ch)
    finally:
        stdscr.nodelay(True)
        with contextlib.suppress(curses.error):
            curses.curs_set(0)


def _draw_screen(
    stdscr: curses.window,
    engine: MochiEngine,
    fb: Framebuffer,
    controller: VoiceController,
    theme_name: str,
    use_ascii: bool,
    show_bezel: bool,
    show_help: bool,
) -> None:
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()

    has_color = curses.has_colors()
    attr_oled = curses.color_pair(PAIR_OLED) | curses.A_BOLD if has_color else curses.A_BOLD
    attr_dim = (
        curses.color_pair(PAIR_OLED_DIM) | (0 if curses.COLORS >= 256 else curses.A_DIM)
        if has_color
        else curses.A_DIM
    )
    attr_bezel = curses.color_pair(PAIR_BEZEL) if has_color else curses.A_NORMAL
    attr_hud = curses.color_pair(PAIR_HUD) if has_color else curses.A_NORMAL
    attr_accent = curses.color_pair(PAIR_HUD_ACCENT) | curses.A_BOLD if has_color else curses.A_BOLD
    attr_warn = curses.color_pair(PAIR_WARN) | curses.A_BOLD if has_color else curses.A_BOLD

    with controller.lock:
        status_line = controller.status_line
        last_user = controller.last_user
        last_mochi = controller.last_mochi
        is_error = controller.is_error
        tts_on = controller.tts_enabled
        hands_free = controller.hands_free
        voice_label = controller.voice.label
        active_b = controller.llm.active_backend()
        allowed = models_for_backend(active_b)
        shown_model = (
            controller.llm.model
            if (controller.llm.model in allowed or ":" in controller.llm.model or "/" in controller.llm.model)
            else allowed[0]
        )
        model_short = shown_model.split("/")[-1]
        vstate = controller.voice_state

    extra_hud = 6 if show_help else 2
    compact = max_x < (72 if show_bezel else 66) or max_y < ((21 if show_bezel else 16) + 2)
    render_fb = fb.downsample_half() if compact else fb

    screen_w = render_fb.width
    screen_h = render_fb.height // 2
    bezel = build_bezel(screen_w, compact=compact) if show_bezel else None

    total_w = bezel.total_width if bezel else screen_w
    total_h = screen_h + (bezel.top_height + bezel.bottom_height if bezel else 0)

    hud_lines = extra_hud if max_y >= total_h + extra_hud else max(1, max_y - total_h)
    if max_x < screen_w + 2 or max_y < screen_h + 2:
        msg = f"Terminal terlalu kecil ({max_x}x{max_y}). Minimal {screen_w + 2}x{screen_h + 2}."
        _safe_addstr(stdscr, max(0, max_y // 2), max(0, (max_x - len(msg)) // 2), msg, attr_accent)
        stdscr.noutrefresh()
        curses.doupdate()
        return

    origin_x = max(0, (max_x - total_w) // 2)
    origin_y = max(0, (max_y - total_h - hud_lines) // 2)

    screen_x = origin_x + (len(bezel.left_pad) if bezel else 0)
    screen_y = origin_y + (bezel.top_height if bezel else 0)

    if bezel:
        for idx, line in enumerate(bezel.top_lines):
            _safe_addstr(stdscr, origin_y + idx, origin_x, line, attr_bezel)
        for row_idx in range(screen_h):
            _safe_addstr(stdscr, screen_y + row_idx, origin_x, bezel.left_pad, attr_bezel)
            _safe_addstr(stdscr, screen_y + row_idx, screen_x + screen_w, bezel.right_pad, attr_bezel)
        for idx, line in enumerate(bezel.bottom_lines):
            _safe_addstr(stdscr, screen_y + screen_h + idx, origin_x, line, attr_bezel)

    # Render OLED pixels
    rows = render_fb.to_ascii_rows() if use_ascii else render_fb.to_half_blocks()
    for row_idx, row_cells in enumerate(rows):
        y = screen_y + row_idx
        if y < 0 or y >= max_y:
            continue
        run_start = 0
        while run_start < len(row_cells):
            is_dim = row_cells[run_start][1]
            run_end = run_start + 1
            while run_end < len(row_cells) and row_cells[run_end][1] == is_dim:
                run_end += 1
            chunk = "".join(cell[0] for cell in row_cells[run_start:run_end])
            _safe_addstr(stdscr, y, screen_x + run_start, chunk, attr_dim if is_dim else attr_oled)
            run_start = run_end

    # Status / HUD bar
    hud_y = min(max_y - hud_lines, origin_y + total_h + (1 if not show_bezel else 0))
    wrap_w = max(24, min(max_x - 4, 72))
    cursor = "▌" if vstate in ("thinking", "speaking") else ""

    if not show_help:
        if is_error:
            shown = _truncate_text(status_line, max_x - 2)
            _safe_addstr(stdscr, hud_y, max(0, (max_x - len(shown)) // 2), shown, attr_warn)
        elif last_mochi:
            sub_lines = textwrap.wrap(last_mochi + cursor, width=wrap_w)[-min(2, hud_lines) :]
            for idx, line in enumerate(sub_lines):
                _safe_addstr(
                    stdscr,
                    hud_y + idx,
                    max(0, (max_x - len(line)) // 2),
                    line,
                    attr_accent if vstate == "speaking" else attr_hud,
                )
        elif vstate in ("listening", "thinking", "speaking") or status_line:
            shown = _truncate_text(status_line, max_x - 2)
            _safe_addstr(stdscr, hud_y, max(0, (max_x - len(shown)) // 2), shown, attr_accent)
        else:
            hf_tag = " [HANDS-FREE]" if hands_free else ""
            hint = f"v: bicara  f: loop  t: ketik  k: jam  h: menu  q: keluar{hf_tag}"
            shown = _truncate_text(hint, max_x - 2)
            _safe_addstr(stdscr, hud_y, max(0, (max_x - len(shown)) // 2), shown, attr_hud)
    else:
        theme = get_theme(theme_name)
        auto_tag = "AUTO" if engine.auto_cycle else "LOCK"
        tts_tag = f"TTS:{voice_label}" if tts_on else "MUTE"
        hf_tag = " LOOP" if hands_free else ""
        clk_tag = " CLOCK" if engine.clock_mode else ""
        state_tag = f" | {vstate.upper()}" if vstate != "none" else ""
        status_hdr = (
            f" [{engine.mood.upper()}{state_tag}]  {model_short}  {tts_tag}  {theme.label}  {auto_tag}{hf_tag}{clk_tag} "
        )
        _safe_addstr(
            stdscr,
            hud_y,
            max(0, (max_x - len(status_hdr)) // 2),
            _truncate_text(status_hdr, max_x - 2),
            attr_accent,
        )

        line_idx = 1
        reserve_footer = 2
        avail_caption = max(0, hud_lines - line_idx - reserve_footer - (1 if last_user and not is_error else 0))
        if avail_caption >= 1 and hud_y + line_idx < max_y:
            if last_mochi and not is_error:
                wrapped = textwrap.wrap(last_mochi + cursor, width=wrap_w)[-min(2, avail_caption) :]
                cap_attr = attr_accent if vstate == "speaking" else attr_hud
            else:
                wrapped = [_truncate_text(status_line or "Menu terbuka (tekan 'h' untuk menutup)", max_x - 2)]
                cap_attr = attr_warn if is_error else attr_accent
            for wline in wrapped:
                if hud_y + line_idx < max_y - reserve_footer:
                    _safe_addstr(stdscr, hud_y + line_idx, max(0, (max_x - len(wline)) // 2), wline, cap_attr)
                    line_idx += 1

        if line_idx < hud_lines - reserve_footer and hud_y + line_idx < max_y - reserve_footer:
            sub = f"You: {last_user}" if (last_user and not is_error) else ""
            if sub:
                shown_sub = _truncate_text(sub, max_x - 2)
                _safe_addstr(stdscr, hud_y + line_idx, max(0, (max_x - len(shown_sub)) // 2), shown_sub, attr_hud)
                line_idx += 1

        footer_y = min(max_y - 2, hud_y + line_idx)
        if footer_y >= 0 and footer_y + 1 < max_y:
            mood_bar = " ".join(f"{m.key}:{m.name}" for m in MOODS)
            controls = "v:mic f:loop t:chat x:stop space:boop k:jam o:model g:voice s:mute c:color h:tutup q:quit"
            _safe_addstr(
                stdscr,
                footer_y,
                max(0, (max_x - len(mood_bar)) // 2),
                _truncate_text(mood_bar, max_x - 2),
                attr_hud,
            )
            _safe_addstr(
                stdscr,
                footer_y + 1,
                max(0, (max_x - len(controls)) // 2),
                _truncate_text(controls, max_x - 2),
                attr_hud,
            )

    stdscr.noutrefresh()
    curses.doupdate()


def run_tui(
    mood: str = "idle",
    theme_name: str = "cyan",
    auto_cycle: bool = True,
    use_ascii: bool = False,
    show_bezel: bool = True,
    fps: int = 24,
    model: str = DEFAULT_FREE_MODEL,
    voice_arg: str = DEFAULT_VOICE_KEY,
    lang_override: str | None = None,
    pitch_override: str | None = None,
    rate_override: str | None = None,
    speed_override: float | None = None,
    tts_enabled: bool = True,
    standalone: bool = False,
    backend: str = "auto",
    reasoning_effort: str = "none",
    start_talking: bool = False,
    hands_free: bool = False,
    clock_mode: bool = False,
    persist_memory: bool = True,
) -> int:
    """Launch the interactive terminal UI."""

    def _main(stdscr: curses.window) -> int:
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)

        nonlocal theme_name, use_ascii, show_bezel
        _init_colors(theme_name)

        voice_preset = resolve_voice(
            voice_arg,
            lang_override=lang_override,
            pitch_override=pitch_override,
            rate_override=rate_override,
            speed_override=speed_override,
        )
        engine = MochiEngine(
            mood=mood,
            auto_cycle=auto_cycle,
            clock_mode=clock_mode,
            battery_pct=get_battery_percent(),
        )
        llm = OpenCodeClient(
            model=model,
            lang=voice_preset.lang,
            standalone=standalone,
            backend=backend,
            reasoning_effort=reasoning_effort,
            persist_memory=persist_memory,
        )
        controller = VoiceController(
            engine=engine,
            llm=llm,
            voice=voice_preset,
            tts_enabled=tts_enabled,
            hands_free=hands_free,
        )
        controller.refresh_models_async()

        fb = Framebuffer(64, 32)
        show_help = False
        frame_dt = 1.0 / max(5, min(fps, 60))
        last_time = time.monotonic()
        last_bat_check = last_time

        if start_talking or hands_free:
            controller.start_voice_turn()

        running = True

        def _handle_sigint(_sig: int, _frame: object) -> None:
            nonlocal running
            running = False

        signal.signal(signal.SIGINT, _handle_sigint)

        try:
            while running:
                now = time.monotonic()
                dt = now - last_time
                last_time = now

                if now - last_bat_check >= 30.0:
                    last_bat_check = now
                    engine.battery_pct = get_battery_percent()

                while True:
                    try:
                        key = stdscr.getch()
                    except curses.error:
                        key = -1
                    if key == -1:
                        break
                    if key in (ord("q"), ord("Q"), 27):
                        running = False
                        break
                    if key in (ord("v"), ord("V"), ord("r"), ord("R")):
                        if controller.is_listening():
                            controller.stop_recording()
                        else:
                            controller.start_voice_turn()
                    elif key in (ord("f"), ord("F")):
                        controller.toggle_hands_free()
                    elif key in (ord("x"), ord("X")):
                        controller.stop_current_turn()
                    elif key in (ord("t"), ord("T")):
                        if not controller.is_busy() or controller.interrupt_speaking():
                            typed = _prompt_text_input(stdscr)
                            last_time = time.monotonic()
                            if typed:
                                controller.start_text_turn(typed)
                    elif key in (ord("k"), ord("K")):
                        engine.clock_mode = not engine.clock_mode
                        engine.battery_pct = get_battery_percent()
                    elif key in (ord("o"), ord("O")):
                        controller.cycle_model()
                    elif key in (ord("g"), ord("G")):
                        controller.cycle_voice()
                    elif key in (ord("s"), ord("S")):
                        controller.toggle_tts()
                    elif key == ord(" "):
                        engine.trigger_boop()
                    elif key in (curses.KEY_RIGHT, ord("n"), ord("N"), ord("l")):
                        engine.next_mood()
                    elif key in (curses.KEY_LEFT, ord("p"), ord("P"), ord("j")):
                        engine.prev_mood()
                    elif key in (ord("c"), ord("C")):
                        theme_name = next_theme(theme_name)
                        _init_colors(theme_name)
                    elif key in (ord("a"), ord("A")):
                        engine.auto_cycle = not engine.auto_cycle
                    elif key in (ord("b"), ord("B")):
                        show_bezel = not show_bezel
                    elif key in (ord("m"), ord("M")):
                        use_ascii = not use_ascii
                    elif key in (ord("h"), ord("H"), ord("?")):
                        show_help = not show_help
                    elif 0 <= key <= 255 and chr(key) in MOOD_BY_KEY:
                        engine.set_mood(MOOD_BY_KEY[chr(key)])

                if not running:
                    break

                controller.sync_to_engine()
                engine.step(dt)
                engine.render(fb)
                _draw_screen(stdscr, engine, fb, controller, theme_name, use_ascii, show_bezel, show_help)

                elapsed_frame = time.monotonic() - now
                sleep_for = frame_dt - elapsed_frame
                if sleep_for > 0:
                    time.sleep(sleep_for)
        finally:
            controller.cancel_all()

        return 0

    return curses.wrapper(_main)
