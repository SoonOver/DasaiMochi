"""OpenCode CLI integration using free models (`opencode/*-free`, `big-pickle`)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from .animations import MOOD_NAMES


FREE_MODELS: tuple[str, ...] = (
    "opencode/beam-free",
    "opencode/hy4-flash-free",
    "opencode/deepseek-v4.1-flash-free",
    "opencode/qwen4-max-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/flint-40b-free",
    "opencode/iceman-free",
    "opencode/big-pickle",
)

DEFAULT_FREE_MODEL = FREE_MODELS[0]

_MOOD_TAG_RE = re.compile(r"\[mood:([a-zA-Z0-9_-]+)\]", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


class OpenCodeError(RuntimeError):
    """Raised when OpenCode CLI invocation or parsing fails."""


@dataclass(frozen=True)
class OpenCodeReply:
    """Structured response from an OpenCode free model turn."""

    text: str
    spoken_text: str
    mood: str | None
    session_id: str | None
    model: str


def build_persona_prompt(lang: str = "id-ID") -> str:
    """Return a concise persona instruction so Mochi replies naturally for TTS."""
    mood_list = ", ".join(MOOD_NAMES)
    low = lang.lower()
    if low.startswith("ja"):
        return (
            "あなたは机の上の小さなかわいいロボット『ダサイモチ』です。"
            "アニメキャラのように可愛く元気な日本語で、1〜2文で短く話してください。"
            "マークダウン、箇条書き、絵文字は使わないでください。"
            f"返答の先頭に `[mood:<name>]`（候補: {mood_list}）を1つ付け、半角スペースの後にお話しください。"
        )
    if low.startswith("en"):
        return (
            "You are Dasai Mochi, a cheerful mini digital robot companion on the user's desk/car. "
            "Reply naturally as spoken conversation in 1 to 3 short sentences. "
            "Do NOT use markdown, bullet lists, code blocks, or emojis because your reply will be read aloud by TTS. "
            f"Start your reply with a mood tag `[mood:<name>]` choosing from: {mood_list}, followed by a space and your answer."
        )
    return (
        "Kamu adalah Dasai Mochi, robot companion mini bergaya JDM yang ramah, lucu, dan ekspresif. "
        "Jawablah santai seperti ngobrol langsung dalam bahasa Indonesia yang natural, cukup 1 sampai 3 kalimat singkat. "
        "JANGAN pakai markdown, bullet list, blok kode, atau emoji karena jawabanmu akan langsung dibacakan lewat Text-to-Speech. "
        f"Awali jawabanmu dengan tag ekspresi `[mood:<nama>]` (pilih satu: {mood_list}), lalu spasi dan langsung isi jawabanmu."
    )


def clean_text_for_speech(text: str) -> str:
    """Strip mood tags and lightweight markdown so TTS sounds natural."""
    cleaned = _MOOD_TAG_RE.sub("", text)
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = cleaned.replace("```", " ").replace("`", "")
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_mood_and_text(raw_reply: str) -> tuple[str | None, str]:
    """Extract `[mood:<name>]` if valid and return `(mood, clean_text)`."""
    detected_mood: str | None = None
    for match in _MOOD_TAG_RE.finditer(raw_reply):
        candidate = match.group(1).strip().lower()
        if candidate in MOOD_NAMES:
            detected_mood = candidate
            break
    display_text = _MOOD_TAG_RE.sub("", raw_reply).strip()
    return detected_mood, display_text


def parse_opencode_json_output(raw_stdout: str) -> tuple[str, str | None]:
    """Parse NDJSON output emitted by `opencode run --format json`."""
    texts: list[str] = []
    session_id: str | None = None
    errors: list[str] = []
    parsed_any_json = False

    for line in raw_stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        parsed_any_json = True

        if not session_id and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]

        ev_type = event.get("type")
        if ev_type == "text":
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(event.get("text"), str):
                texts.append(event["text"])
        elif ev_type == "error":
            err = event.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or str(err)
            else:
                msg = str(err or event.get("message") or "Unknown OpenCode error")
            errors.append(msg)

    joined = "".join(texts).strip()
    if joined:
        return joined, session_id
    if errors:
        raise OpenCodeError(errors[-1])
    if not parsed_any_json:
        plain = raw_stdout.strip()
        if plain:
            return plain, session_id
    raise OpenCodeError("OpenCode tidak mengembalikan teks jawaban.")


def discover_free_models(opencode_bin: str = "opencode") -> tuple[str, ...]:
    """List free OpenCode models dynamically if CLI responds quickly, else return defaults."""
    if not shutil.which(opencode_bin):
        return FREE_MODELS
    try:
        proc = subprocess.run(
            [opencode_bin, "models", "opencode"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return FREE_MODELS

    if proc.returncode != 0:
        return FREE_MODELS

    found = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip().startswith("opencode/")
        and (line.strip().endswith("-free") or line.strip() == "opencode/big-pickle")
    ]
    if not found:
        return FREE_MODELS

    # Keep curated preferred order first, then any additional discovered free models
    ordered = [m for m in FREE_MODELS if m in found]
    for m in found:
        if m not in ordered:
            ordered.append(m)
    return tuple(ordered)


@dataclass
class OpenCodeClient:
    """Stateful conversational client backed by `opencode run`."""

    model: str = DEFAULT_FREE_MODEL
    lang: str = "id-ID"
    standalone: bool = False
    timeout: float = 60.0
    opencode_bin: str = "opencode"
    session_id: str | None = field(default=None)

    def reset_session(self) -> None:
        self.session_id = None

    def set_model(self, model: str) -> None:
        self.model = model
        self.reset_session()

    def ask(
        self,
        user_message: str,
        on_partial: Callable[[str, str | None], None] | None = None,
    ) -> OpenCodeReply:
        message = user_message.strip()
        if not message:
            raise ValueError("Pesan tidak boleh kosong.")
        if not shutil.which(self.opencode_bin):
            raise OpenCodeError(f"Binary '{self.opencode_bin}' tidak ditemukan di PATH.")

        prompt = message if self.session_id else f"{build_persona_prompt(self.lang)}\n\nUser: {message}"

        cmd = [
            self.opencode_bin,
            "run",
            "--format",
            "json",
            "--model",
            self.model,
            "--archived",
        ]
        if self.standalone:
            cmd.append("--standalone")
        if self.session_id:
            cmd.extend(["--session", self.session_id])
        cmd.append(prompt)

        if on_partial is None:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OpenCodeError(f"OpenCode timeout setelah {int(self.timeout)} detik.") from exc
            except OSError as exc:
                raise OpenCodeError(f"Gagal menjalankan opencode: {exc}") from exc

            if proc.returncode != 0 and not proc.stdout.strip():
                detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
                raise OpenCodeError(f"OpenCode gagal: {detail}")

            raw_text, sid = parse_opencode_json_output(proc.stdout)
        else:
            raw_text, sid = self._ask_streaming(cmd, on_partial)

        if sid:
            self.session_id = sid

        mood, display_text = extract_mood_and_text(raw_text)
        spoken = clean_text_for_speech(display_text)
        if not display_text:
            display_text = spoken or raw_text

        return OpenCodeReply(
            text=display_text,
            spoken_text=spoken or display_text,
            mood=mood,
            session_id=self.session_id,
            model=self.model,
        )

    def _ask_streaming(
        self,
        cmd: list[str],
        on_partial: Callable[[str, str | None], None],
    ) -> tuple[str, str | None]:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise OpenCodeError(f"Gagal menjalankan opencode: {exc}") from exc

        assert proc.stdout is not None
        texts: list[str] = []
        raw_lines: list[str] = []
        errors: list[str] = []
        sid: str | None = None

        try:
            for line in proc.stdout:
                raw_lines.append(line)
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if not sid and isinstance(event.get("sessionID"), str):
                    sid = event["sessionID"]

                ev_type = event.get("type")
                if ev_type == "text":
                    part = event.get("part")
                    chunk = part.get("text") if isinstance(part, dict) else event.get("text")
                    if isinstance(chunk, str) and chunk:
                        texts.append(chunk)
                        mood, partial_display = extract_mood_and_text("".join(texts))
                        if partial_display:
                            on_partial(partial_display, mood)
                elif ev_type == "error":
                    err = event.get("error")
                    msg = err.get("message") if isinstance(err, dict) else str(err or "Unknown OpenCode error")
                    errors.append(str(msg))
            rc = proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise OpenCodeError(f"OpenCode timeout setelah {int(self.timeout)} detik.") from exc

        if texts:
            return "".join(texts).strip(), sid
        if errors:
            raise OpenCodeError(errors[-1])
        joined_raw = "".join(raw_lines)
        if rc != 0 and not joined_raw.strip():
            err_out = proc.stderr.read().strip() if proc.stderr else ""
            raise OpenCodeError(f"OpenCode gagal: {err_out or f'exit code {rc}'}")
        return parse_opencode_json_output(joined_raw)
