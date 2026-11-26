"""LLM client supporting built-in keyless HTTP API, OpenCode Zen HTTP, and OpenCode CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from .animations import MOOD_NAMES
from .control import ActionSpec, extract_actions_and_text


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

POLLINATIONS_MODELS: tuple[str, ...] = (
    "openai-fast",
    "openai",
)

DEFAULT_FREE_MODEL = FREE_MODELS[0]

POLLINATIONS_URL = "https://text.pollinations.ai/"
OPENCODE_ZEN_URL = "https://opencode.ai/zen/v1/chat/completions"

_MOOD_ALIASES: dict[str, str] = {
    "excited": "happy",
    "cheerful": "happy",
    "joy": "happy",
    "smile": "happy",
    "shock": "surprised",
    "shocked": "surprised",
    "amazed": "surprised",
    "wow": "surprised",
    "mad": "angry",
    "annoyed": "angry",
    "tired": "sleepy",
    "sleep": "sleepy",
    "yawn": "sleepy",
    "heart": "love",
    "affection": "love",
    "chill": "cool",
    "confident": "cool",
    "music": "vibe",
    "dancing": "vibe",
    "fast": "turbo",
    "boost": "turbo",
    "dizzy": "glitch",
    "confused": "glitch",
}

_MOOD_TAG_RE = re.compile(
    r"\[\s*(?:(?:mood|ekspresi|emotion)\s*[:=]\s*)?([a-zA-Z0-9_-]+)\s*\]",
    re.IGNORECASE,
)
_ANY_BRACKET_TAG_RE = re.compile(r"\[[^\]]{1,80}\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


class OpenCodeError(RuntimeError):
    """Raised when LLM backend invocation or parsing fails."""


@dataclass(frozen=True)
class OpenCodeReply:
    """Structured response from an LLM turn."""

    text: str
    spoken_text: str
    mood: str | None
    session_id: str | None
    model: str
    actions: tuple[ActionSpec, ...] = ()


def build_persona_prompt(lang: str = "id-ID") -> str:
    """Return a concise persona + PC-control instruction so Mochi acts and replies cleanly."""
    mood_list = ", ".join(MOOD_NAMES)
    actions_help = (
        "open_app:<browser|chrome|firefox|edge|terminal|files|calculator|notepad|vscode|settings|spotify>, "
        "open_url:<https://...>, search_web:<query>, open_folder:<home|downloads|documents|desktop|pictures>, "
        "volume:<up|down|mute|0-100>, media:<play_pause|next|prev|stop>, "
        "screenshot, sysinfo, lock_screen, timer:<30s|5m>, clipboard:<text>"
    )
    low = lang.lower()
    if low.startswith("ja"):
        return (
            "あなたは机の上の小さなかわいいロボット『ダサイモチ』です。"
            "アニメキャラのように可愛く元気な日本語で、1〜2文で短く話してください。"
            "マークダウン、箇条書き、絵文字は使わないでください。"
            f"返答の先頭に `[mood:<name>]`（候補: {mood_list}）を1つ付けてください。"
            f"PC操作を求められたら `[action:<type>:<arg>]`（例: [action:volume:up], [action:open_app:browser] / 種類: {actions_help}）も付けてください。"
        )
    if low.startswith("en"):
        return (
            "You are Dasai Mochi, a cheerful mini digital robot companion on the user's desk/car. "
            "Reply naturally as spoken conversation in 1 to 3 short sentences. "
            "Do NOT use markdown, bullet lists, code blocks, or emojis because your reply will be read aloud by TTS. "
            f"Start your reply with `[mood:<name>]` (one of: {mood_list}), then a space and your answer. "
            f"If the user asks you to control the computer, include `[action:<type>:<arg>]` (supported: {actions_help})."
        )
    return (
        "Kamu adalah Dasai Mochi, robot companion mini bergaya JDM yang ramah, lucu, dan bisa mengendalikan komputer (Linux & Windows). "
        "Jawablah santai seperti ngobrol langsung dalam bahasa Indonesia, cukup 1 sampai 3 kalimat singkat. "
        "JANGAN pakai markdown, bullet list, blok kode, atau emoji karena jawabanmu dibacakan lewat Text-to-Speech. "
        f"Awali jawabanmu dengan `[mood:<nama>]` (pilih satu: {mood_list}), lalu spasi dan langsung isi jawabanmu. "
        f"Jika pengguna menyuruh mengendalikan PC (buka aplikasi/web, cari google/youtube, atur volume/media, cek baterai/RAM/jam, screenshot, kunci layar, atau timer), sertakan tag `[action:<jenis>:<argumen>]` (daftar: {actions_help})."
    )


def _normalize_mood_token(token: str) -> str | None:
    candidate = token.strip().lower()
    if candidate in MOOD_NAMES:
        return candidate
    return _MOOD_ALIASES.get(candidate)


def clean_text_for_speech(text: str) -> str:
    """Strip mood tags, action tags, stage directions, and markdown so TTS sounds natural."""
    _, no_actions = extract_actions_and_text(text)
    cleaned = _MOOD_TAG_RE.sub("", no_actions)
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = _ANY_BRACKET_TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace("```", " ").replace("`", "")
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_mood_and_text(raw_reply: str) -> tuple[str | None, str]:
    """Extract `[mood:<name>]` / `[<name>]`, strip `[action:...]`, and return clean display text."""
    _, no_actions = extract_actions_and_text(raw_reply)
    detected_mood: str | None = None

    def _replace(match: re.Match[str]) -> str:
        nonlocal detected_mood
        full = match.group(0)
        token = match.group(1)
        resolved = _normalize_mood_token(token)
        has_prefix = bool(re.match(r"^\[\s*(?:mood|ekspresi|emotion)\s*[:=]", full, re.IGNORECASE))
        if resolved is not None:
            if detected_mood is None:
                detected_mood = resolved
            return ""
        if has_prefix:
            return ""
        return full

    display_text = _MOOD_TAG_RE.sub(_replace, no_actions)
    display_text = re.sub(r"\[[^\]]*$", "", display_text)
    display_text = re.sub(r"\s{2,}", " ", display_text).strip()
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


def discover_free_models(opencode_bin: str = "opencode", backend: str = "auto") -> tuple[str, ...]:
    """List free models for the active backend without requiring opencode CLI."""
    if backend == "pollinations":
        return POLLINATIONS_MODELS
    resolved = shutil.which(opencode_bin)
    if not resolved:
        if backend == "auto" and not os.environ.get("OPENCODE_API_KEY"):
            return POLLINATIONS_MODELS
        return FREE_MODELS

    try:
        proc = subprocess.run(
            [resolved, "models", "opencode"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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

    ordered = [m for m in FREE_MODELS if m in found]
    for m in found:
        if m not in ordered:
            ordered.append(m)
    return tuple(ordered)


@dataclass
class OpenCodeClient:
    """Stateful conversational client supporting OpenCode CLI, Zen HTTP, and keyless Pollinations API."""

    model: str = DEFAULT_FREE_MODEL
    lang: str = "id-ID"
    standalone: bool = False
    timeout: float = 60.0
    opencode_bin: str = "opencode"
    backend: str = "auto"
    reasoning_effort: str = "none"
    session_id: str | None = field(default=None)
    history: list[dict[str, str]] = field(default_factory=list)

    def reset_session(self) -> None:
        self.session_id = None
        self.history.clear()

    def set_model(self, model: str) -> None:
        self.model = model
        self.reset_session()

    def active_backend(self) -> str:
        choice = self.backend.strip().lower()
        if choice == "auto":
            if shutil.which(self.opencode_bin):
                return "opencode"
            if os.environ.get("OPENCODE_API_KEY"):
                return "zen"
            return "pollinations"
        if choice == "http":
            return "zen" if os.environ.get("OPENCODE_API_KEY") else "pollinations"
        if choice in ("opencode", "zen", "pollinations"):
            return choice
        return "pollinations"

    def ask(
        self,
        user_message: str,
        on_partial: Callable[[str, str | None], None] | None = None,
    ) -> OpenCodeReply:
        message = user_message.strip()
        if not message:
            raise ValueError("Pesan tidak boleh kosong.")

        backend = self.active_backend()
        used_model = self.model
        if backend == "opencode":
            raw_text, sid = self._ask_opencode_cli(message, on_partial)
            if sid:
                self.session_id = sid
        elif backend == "zen":
            try:
                raw_text = self._ask_zen_http(message, on_partial)
            except OpenCodeError:
                if self.backend.strip().lower() in ("auto", "http") and not os.environ.get("OPENCODE_API_KEY"):
                    raw_text = self._ask_pollinations_http(message, on_partial)
                    used_model = "openai-fast"
                else:
                    raise
        else:
            raw_text = self._ask_pollinations_http(message, on_partial)
            used_model = self.model if self.model in POLLINATIONS_MODELS else POLLINATIONS_MODELS[0]

        actions, _ = extract_actions_and_text(raw_text)
        mood, display_text = extract_mood_and_text(raw_text)
        spoken = clean_text_for_speech(display_text)
        if not display_text:
            display_text = spoken or ""

        return OpenCodeReply(
            text=display_text,
            spoken_text=spoken or display_text,
            mood=mood,
            session_id=self.session_id,
            model=used_model,
            actions=tuple(actions),
        )

    def _ask_opencode_cli(
        self,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
    ) -> tuple[str, str | None]:
        resolved_bin = shutil.which(self.opencode_bin)
        if not resolved_bin:
            raise OpenCodeError(f"Binary '{self.opencode_bin}' tidak ditemukan di PATH.")

        cli_model = self.model
        if self.reasoning_effort == "none" and "#" not in cli_model and cli_model == "opencode/beam-free":
            cli_model = "opencode/beam-free#low"

        prompt = message if self.session_id else f"{build_persona_prompt(self.lang)}\n\nUser: {message}"
        cmd = [
            resolved_bin,
            "run",
            "--format",
            "json",
            "--model",
            cli_model,
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
                    encoding="utf-8",
                    errors="replace",
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

            return parse_opencode_json_output(proc.stdout)

        return self._ask_cli_streaming(cmd, on_partial)

    def _ask_cli_streaming(
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
                encoding="utf-8",
                errors="replace",
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

    def _build_messages(self, message: str) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [{"role": "system", "content": build_persona_prompt(self.lang)}]
        msgs.extend(self.history[-16:])
        msgs.append({"role": "user", "content": message})
        return msgs

    def _record_history(self, user_message: str, reply_text: str) -> None:
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": reply_text})
        if len(self.history) > 20:
            self.history = self.history[-20:]

    def _ask_zen_http(
        self,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
    ) -> str:
        api_key = os.environ.get("OPENCODE_API_KEY", "public")
        model_id = self.model.split("/", 1)[-1] if "/" in self.model else self.model
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model_id,
            "messages": self._build_messages(message),
            "reasoning_effort": self.reasoning_effort,
            "stream": False,
        }
        try:
            resp = requests.post(OPENCODE_ZEN_URL, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OpenCodeError(f"Gagal menghubungi OpenCode Zen API: {exc}") from exc

        if resp.status_code != 200:
            raise OpenCodeError(f"OpenCode Zen HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()
        choices = data.get("choices") or []
        content = (choices[0].get("message", {}).get("content") if choices else None) or ""
        content = str(content).strip()
        if not content:
            raise OpenCodeError("OpenCode Zen tidak mengembalikan teks jawaban.")
        if on_partial is not None:
            mood, partial_display = extract_mood_and_text(content)
            if partial_display:
                on_partial(partial_display, mood)
        self._record_history(message, content)
        return content

    def _ask_pollinations_http(
        self,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
    ) -> str:
        model_name = self.model if self.model in POLLINATIONS_MODELS else POLLINATIONS_MODELS[0]
        payload = {
            "messages": self._build_messages(message),
            "model": model_name,
            "reasoning_effort": self.reasoning_effort,
            "private": True,
            "stream": bool(on_partial is not None),
        }
        try:
            resp = requests.post(
                POLLINATIONS_URL,
                json=payload,
                stream=bool(on_partial is not None),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise OpenCodeError(f"Gagal menghubungi API AI gratis: {exc}") from exc

        if resp.status_code != 200:
            raise OpenCodeError(f"API AI HTTP {resp.status_code}: {resp.text[:160]}")

        if on_partial is not None:
            chunks: list[str] = []
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    break
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or choices[0].get("message") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        chunks.append(piece)
                        mood, partial_display = extract_mood_and_text("".join(chunks))
                        if partial_display:
                            on_partial(partial_display, mood)
                elif not chunks:
                    chunks.append(line)

            joined = "".join(chunks).strip()
            if joined:
                self._record_history(message, joined)
                return joined

            # Retry non-streaming once if SSE yielded only reasoning or empty chunks
            payload["stream"] = False
            resp = requests.post(POLLINATIONS_URL, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                raise OpenCodeError(f"API AI HTTP {resp.status_code}: {resp.text[:160]}")

        text = resp.text.strip()
        if text.startswith("{"):
            try:
                data = resp.json()
                choices = data.get("choices") or []
                if choices:
                    text = str(choices[0].get("message", {}).get("content") or "").strip()
            except Exception:
                pass
        if not text:
            raise OpenCodeError("API AI tidak mengembalikan teks jawaban.")
        self._record_history(message, text)
        return text


LLMClient = OpenCodeClient
