"""Interactive curses TUI for Dasai Mochi with voice chat & OpenCode integration."""

from __future__ import annotations

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
    AudioError,
    DEFAULT_VOICE_KEY,
    VoicePreset,
    next_voice_preset,
    record_mic_to_wav,
    resolve_voice,
    speak_text,
    transcribe_wav,
)
from .control import execute_action
from .framebuffer import Framebuffer, build_bezel
from .llm import DEFAULT_FREE_MODEL, FREE_MODELS, POLLINATIONS_MODELS, OpenCodeClient, OpenCodeError, discover_free_models
from .themes import Theme, get_theme, next_theme


# Color pair IDs
PAIR_OLED = 1
PAIR_OLED_DIM = 2
PAIR_BEZEL = 3
PAIR_HUD = 4
PAIR_HUD_ACCENT = 5
PAIR_WARN = 6


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
    try:
        win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 3 or len(text) <= limit:
        return text[: max(0, limit)]
    return text[: limit - 1] + "…"


@dataclass
class VoiceController:
    """Thread-safe coordinator between the 24 FPS TUI and voice/OpenCode tasks."""

    engine: MochiEngine
    llm: OpenCodeClient
    voice: VoicePreset
    tts_enabled: bool = True
    models: tuple[str, ...] = FREE_MODELS

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

    def cancel_all(self) -> None:
        with self.lock:
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

    def cycle_model(self) -> str:
        with self.lock:
            fallback = POLLINATIONS_MODELS if self.llm.active_backend() == "pollinations" else FREE_MODELS
            models = self.models or fallback
            idx = models.index(self.llm.model) if self.llm.model in models else -1
            nxt = models[(idx + 1) % len(models)]
            self.llm.set_model(nxt)
            self.status_line = f"Model ({self.llm.active_backend()}): {nxt} (sesi baru)"
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

    def _run_llm_and_tts(self, user_text: str, cancel_ev: threading.Event) -> None:
        with self.lock:
            self.voice_state = "thinking"
            self.mic_level = 0.0
            self.last_user = user_text
            self.last_mochi = ""
            self.is_error = False
            active_b = self.llm.active_backend()
            model_short = (
                self.llm.model
                if active_b != "pollinations" or self.llm.model in POLLINATIONS_MODELS
                else POLLINATIONS_MODELS[0]
            ).split("/")[-1]
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

        # Execute any requested cross-platform computer control actions
        action_notes: list[str] = []
        if reply.actions:
            def _on_timer(msg: str) -> None:
                with self.lock:
                    self.engine.set_mood("surprised")
                    self.last_mochi = msg
                    tts_flag = self.tts_enabled
                    v = self.voice
                if tts_flag:
                    threading.Thread(
                        target=lambda: speak_text(msg, voice=v, display_text=msg),
                        daemon=True,
                    ).start()

            for spec in reply.actions:
                res = execute_action(spec, on_timer_done=_on_timer)
                if res.kind == "sysinfo" or not res.ok:
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

            def _on_end() -> None:
                with self.lock:
                    if self.voice_state == "speaking":
                        self.voice_state = "none"
                    if not self.last_mochi and not self.is_error:
                        self.last_mochi = final_text
                    self.status_line = ""

            speak_text(
                final_spoken,
                voice=voice,
                on_start=_on_start,
                on_word=_on_word,
                on_end=_on_end,
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
            self._busy = True
            self._cancel_event = threading.Event()
            cancel_ev = self._cancel_event
            stop_ev = threading.Event()
            self._rec_stop_event = stop_ev
            self.voice_state = "listening"
            self.mic_level = 0.0
            self.is_error = False
            self.status_line = "Mendengarkan mic... bicara sekarang ('v' untuk kirim cepat)"
            lang = self.voice.lang

        def _worker() -> None:
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

                self._run_llm_and_tts(transcript, cancel_ev)
            except (AudioError, OpenCodeError, ValueError) as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.mic_level = 0.0
                    self.is_error = True
                    self.status_line = str(exc)
            except Exception as exc:
                with self.lock:
                    self.voice_state = "none"
                    self.mic_level = 0.0
                    self.is_error = True
                    self.status_line = f"Error tak terduga: {exc}"
            finally:
                with self.lock:
                    if self.voice_state in ("listening", "thinking", "speaking"):
                        self.voice_state = "none"
                    self.mic_level = 0.0
                    self._rec_stop_event = None
                    self._busy = False
                if os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except OSError:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    def start_text_turn(self, user_text: str) -> None:
        cleaned = user_text.strip()
        if not cleaned:
            return
        with self.lock:
            if self._busy:
                self.status_line = "Tunggu proses sebelumnya selesai..."
                return
            self._busy = True
            self._cancel_event = threading.Event()
            cancel_ev = self._cancel_event

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

        threading.Thread(target=_worker, daemon=True).start()


def _prompt_text_input(stdscr: curses.window, max_len: int = 220) -> str | None:
    """Temporarily collect a line of text at the bottom of the terminal."""
    max_y, max_x = stdscr.getmaxyx()
    prompt_y = max(0, max_y - 1)
    prefix = "Chat > "
    buf: list[str] = []

    stdscr.nodelay(False)
    try:
        try:
            curses.curs_set(1)
        except curses.error:
            pass

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
        try:
            curses.curs_set(0)
        except curses.error:
            pass


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
        voice_label = controller.voice.label
        model_short = controller.llm.model.split("/")[-1]
        vstate = controller.voice_state

    extra_hud = 5 if show_help else 2
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

    # Status / HUD bar (minimal by default; full legend only when menu is opened via 'h')
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
            hint = "v: bicara  t: ketik  h: menu  q: keluar"
            shown = _truncate_text(hint, max_x - 2)
            _safe_addstr(stdscr, hud_y, max(0, (max_x - len(shown)) // 2), shown, attr_hud)
    else:
        theme = get_theme(theme_name)
        auto_tag = "AUTO" if engine.auto_cycle else "LOCK"
        tts_tag = f"TTS:{voice_label}" if tts_on else "MUTE"
        state_tag = f" | {vstate.upper()}" if vstate != "none" else ""
        status_hdr = (
            f" [{engine.mood.upper()}{state_tag}]  {model_short}  {tts_tag}  {theme.label}  {auto_tag} "
        )
        _safe_addstr(
            stdscr,
            hud_y,
            max(0, (max_x - len(status_hdr)) // 2),
            _truncate_text(status_hdr, max_x - 2),
            attr_accent,
        )

        line_idx = 1
        if line_idx < hud_lines and hud_y + line_idx < max_y:
            if last_mochi and not is_error:
                caption = last_mochi + cursor
                caption_attr = attr_accent if vstate == "speaking" else attr_hud
            else:
                caption = status_line or "Menu terbuka (tekan 'h' untuk menutup)"
                caption_attr = attr_warn if is_error else attr_accent
            shown = _truncate_text(caption, max_x - 2)
            _safe_addstr(stdscr, hud_y + line_idx, max(0, (max_x - len(shown)) // 2), shown, caption_attr)
            line_idx += 1

        if line_idx < hud_lines and hud_y + line_idx < max_y:
            sub = f"You: {last_user}" if (last_user and not is_error) else ""
            if sub:
                shown_sub = _truncate_text(sub, max_x - 2)
                _safe_addstr(stdscr, hud_y + line_idx, max(0, (max_x - len(shown_sub)) // 2), shown_sub, attr_hud)
                line_idx += 1

        if line_idx + 1 < hud_lines and hud_y + line_idx + 1 < max_y:
            mood_bar = " ".join(f"{m.key}:{m.name}" for m in MOODS)
            controls = "v:mic  t:chat  space:boop  o:model  g:voice  s:mute  c:color  a:auto  b:bezel  m:ascii  h:tutup  q:quit"
            _safe_addstr(
                stdscr,
                hud_y + line_idx,
                max(0, (max_x - len(mood_bar)) // 2),
                _truncate_text(mood_bar, max_x - 2),
                attr_hud,
            )
            _safe_addstr(
                stdscr,
                hud_y + line_idx + 1,
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
) -> int:
    """Launch the interactive terminal UI."""

    def _main(stdscr: curses.window) -> int:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
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
        engine = MochiEngine(mood=mood, auto_cycle=auto_cycle)
        llm = OpenCodeClient(
            model=model,
            lang=voice_preset.lang,
            standalone=standalone,
            backend=backend,
            reasoning_effort=reasoning_effort,
        )
        controller = VoiceController(
            engine=engine,
            llm=llm,
            voice=voice_preset,
            tts_enabled=tts_enabled,
        )
        controller.refresh_models_async()

        fb = Framebuffer(64, 32)
        show_help = False
        frame_dt = 1.0 / max(5, min(fps, 60))
        last_time = time.monotonic()

        if start_talking:
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
                    elif key in (ord("t"), ord("T")):
                        if not controller.is_busy():
                            typed = _prompt_text_input(stdscr)
                            last_time = time.monotonic()
                            if typed:
                                controller.start_text_turn(typed)
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
