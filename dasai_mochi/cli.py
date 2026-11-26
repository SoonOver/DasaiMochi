"""CLI entrypoint and static ANSI snapshot renderer for Dasai Mochi."""

from __future__ import annotations

import argparse
import os
import sys

from .animations import MOOD_NAMES, MOODS, MochiEngine
from .audio import DEFAULT_VOICE_KEY, VOICE_PRESETS, resolve_voice, speak_text
from .control import execute_action
from .framebuffer import Framebuffer, build_bezel
from .llm import DEFAULT_FREE_MODEL, OpenCodeClient, OpenCodeError, discover_free_models
from .themes import THEME_ORDER, get_theme
from .tui import run_tui


ANSI_RESET = "\033[0m"
ANSI_BG_BLACK = "\033[40m"


def render_snapshot(
    mood: str = "idle",
    theme_name: str = "cyan",
    use_ascii: bool = False,
    show_bezel: bool = True,
    use_color: bool = True,
    t: float = 0.0,
) -> str:
    """Render a single static frame as a multi-line string (ANSI optional)."""
    theme = get_theme(theme_name)
    engine = MochiEngine(mood=mood, auto_cycle=False, seed=42)
    if t > 0:
        steps = max(1, int(round(t / 0.05)))
        step_dt = t / steps
        for _ in range(steps):
            engine.step(step_dt)

    fb = Framebuffer(64, 32)
    engine.render(fb)
    rows = fb.to_ascii_rows() if use_ascii else fb.to_half_blocks()
    bezel = build_bezel(fb.width, compact=False) if show_bezel else None

    lines: list[str] = []
    if bezel:
        b_start = theme.ansi_bezel if use_color else ""
        b_end = ANSI_RESET if use_color else ""
        for line in bezel.top_lines:
            lines.append(f"{b_start}{line}{b_end}")

    for row_cells in rows:
        parts: list[str] = []
        if bezel:
            if use_color:
                parts.append(f"{theme.ansi_bezel}{bezel.left_pad}{ANSI_RESET}{ANSI_BG_BLACK}")
            else:
                parts.append(bezel.left_pad)

        for ch, is_dim in row_cells:
            if use_color:
                fg = theme.ansi_dim if is_dim else theme.ansi_fg
                parts.append(f"{fg}{ANSI_BG_BLACK}{ch}")
            else:
                parts.append(ch)

        if bezel:
            if use_color:
                parts.append(f"{ANSI_RESET}{theme.ansi_bezel}{bezel.right_pad}{ANSI_RESET}")
            else:
                parts.append(bezel.right_pad)
        elif use_color:
            parts.append(ANSI_RESET)

        lines.append("".join(parts))

    if bezel:
        b_start = theme.ansi_bezel if use_color else ""
        b_end = ANSI_RESET if use_color else ""
        for line in bezel.bottom_lines:
            lines.append(f"{b_start}{line}{b_end}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dasai-mochi",
        description="Terminal Dasai Mochi digital companion with OpenCode free LLM & voice chat.",
    )
    parser.add_argument(
        "--mood",
        choices=MOOD_NAMES,
        default="idle",
        help="Starting mood (default: idle)",
    )
    parser.add_argument(
        "--color",
        choices=THEME_ORDER,
        default="cyan",
        help="OLED phosphor color theme (default: cyan)",
    )
    parser.add_argument(
        "--no-auto",
        action="store_true",
        help="Disable automatic mood rotation",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Render using pure ASCII density characters instead of Unicode half-blocks",
    )
    parser.add_argument(
        "--no-bezel",
        action="store_true",
        help="Hide the helmet frame around the OLED screen",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=24,
        help="Animation framerate between 5 and 60 (default: 24)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_FREE_MODEL,
        help=f"AI model ID (default: {DEFAULT_FREE_MODEL}, 1M context keyless free)",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "kilo", "opencode", "zen", "pollinations", "gemini", "groq", "http"),
        default="auto",
        help="AI backend: auto (Kilo 1M-ctx free, or Gemini/Groq/Zen if API key set), kilo, opencode, zen, pollinations",
    )
    parser.add_argument(
        "--reasoning",
        choices=("none", "low", "medium", "high"),
        default="none",
        help="Reasoning effort level for LLM responses (default: none)",
    )
    parser.add_argument(
        "--voice",
        default=DEFAULT_VOICE_KEY,
        help=f"TTS voice preset or Edge TTS voice ID (default: {DEFAULT_VOICE_KEY})",
    )
    parser.add_argument(
        "--lang",
        default=None,
        help="Override STT/prompt language code (e.g. id-ID, en-US, ja-JP)",
    )
    parser.add_argument(
        "--pitch",
        default=None,
        help="Override Edge TTS pitch shift (e.g. +28Hz, +40Hz, -10Hz)",
    )
    parser.add_argument(
        "--rate",
        default=None,
        help="Override Edge TTS speaking rate (e.g. +10%%, +0%%, -5%%)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Override mpv playback speed/formant shift (e.g. 1.18 for chibi anime tone)",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Start with TTS audio muted",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Pass --standalone when invoking opencode run",
    )
    parser.add_argument(
        "--talk",
        action="store_true",
        help="Immediately start listening to microphone on launch",
    )
    parser.add_argument(
        "--ask",
        default=None,
        help="Ask one question via OpenCode free model, print reply (and speak unless --no-tts), then exit",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Print one frame to stdout and exit instead of starting the interactive TUI",
    )
    parser.add_argument(
        "--no-ansi",
        action="store_true",
        help="Disable ANSI color escape codes in --snapshot output",
    )
    parser.add_argument(
        "--list-moods",
        action="store_true",
        help="List all available moods and exit",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List OpenCode free models and exit",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available TTS voice presets and exit",
    )
    return parser


def _configure_windows_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_windows_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_moods:
        for m in MOODS:
            print(f"[{m.key}] {m.name:<10} - {m.description}")
        return 0

    if args.list_models:
        for model in discover_free_models(backend=args.backend):
            marker = " (default)" if model in (DEFAULT_FREE_MODEL, "gemini-2.5-flash", "llama-3.3-70b-versatile", "openai-fast", "opencode/beam-free") else ""
            print(f"{model}{marker}")
        return 0

    if args.list_voices:
        for vp in VOICE_PRESETS:
            default_tag = " (default)" if vp.key == DEFAULT_VOICE_KEY else ""
            print(
                f"{vp.key:<13} [{vp.backend:<8}] -> {vp.voice_id:<18} "
                f"({vp.lang}, speed={vp.playback_speed:.2f}x){default_tag}"
            )
        return 0

    if args.ask is not None:
        voice = resolve_voice(
            args.voice,
            lang_override=args.lang,
            pitch_override=args.pitch,
            rate_override=args.rate,
            speed_override=args.speed,
        )
        client = OpenCodeClient(
            model=args.model,
            lang=voice.lang,
            standalone=args.standalone,
            backend=args.backend,
            reasoning_effort=args.reasoning,
        )
        try:
            reply = client.ask(args.ask)
        except OpenCodeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        extra_notes: list[str] = []
        for spec in reply.actions:
            res = execute_action(spec)
            if res.kind == "sysinfo" or not res.ok:
                extra_notes.append(res.message)
        out_text = f"{reply.text} {' '.join(extra_notes)}".strip() if extra_notes else reply.text
        out_spoken = f"{reply.spoken_text}. {' '.join(extra_notes)}".strip() if extra_notes else reply.spoken_text
        mood_tag = f"[{reply.mood}] " if reply.mood else ""
        print(f"{mood_tag}{out_text}")
        if not args.no_tts:
            speak_text(out_spoken, voice=voice, display_text=out_text)
        return 0

    if args.snapshot:
        use_color = (not args.no_ansi) and sys.stdout.isatty()
        print(
            render_snapshot(
                mood=args.mood,
                theme_name=args.color,
                use_ascii=args.ascii,
                show_bezel=not args.no_bezel,
                use_color=use_color,
            )
        )
        return 0

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Error: interactive TUI requires a TTY terminal (use --snapshot or --ask for non-TTY).", file=sys.stderr)
        return 1

    return run_tui(
        mood=args.mood,
        theme_name=args.color,
        auto_cycle=not args.no_auto,
        use_ascii=args.ascii,
        show_bezel=not args.no_bezel,
        fps=args.fps,
        model=args.model,
        voice_arg=args.voice,
        lang_override=args.lang,
        pitch_override=args.pitch,
        rate_override=args.rate,
        speed_override=args.speed,
        tts_enabled=not args.no_tts,
        standalone=args.standalone,
        backend=args.backend,
        reasoning_effort=args.reasoning,
        start_talking=args.talk,
    )
