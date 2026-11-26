"""LLM client supporting Kilo 1M-context keyless API, optional Gemini/Groq/Zen keys, Pollinations, and OpenCode CLI."""

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
from .memory import MemoryStore, extract_memory_tags

KILO_MODELS: tuple[str, ...] = (
    "reflection/beam-free",                  # 1,048,576 ctx, reliable Indonesian & actions
    "nvidia/nemotron-3.5-lightning:free",    # 1,000,000 ctx, ~0.8s ultra-fast
    "nvidia/nemotron-3.5-super-vl:free",     # 1,000,000 ctx
    "inclusionai/ling-3.2-flash:free",       # 1,048,576 ctx
    "kilo-auto/free",                        #   256,000 ctx, auto router
)

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

GEMINI_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)

GROQ_MODELS: tuple[str, ...] = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
)

DEFAULT_FREE_MODEL = KILO_MODELS[0]

KILO_URL = "https://api.kilo.ai/api/gateway/chat/completions"
POLLINATIONS_URL = "https://text.pollinations.ai/"
OPENCODE_ZEN_URL = "https://opencode.ai/zen/v1/chat/completions"
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

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
    "sad": "sad",
    "cry": "sad",
    "unhappy": "sad",
    "sorry": "sad",
    "wink": "wink",
    "playful": "wink",
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


def build_persona_prompt(lang: str = "id-ID", remembered_facts: list[str] | None = None) -> str:
    """Return a concise persona + PC-control instruction so Mochi acts and replies cleanly."""
    mood_list = ", ".join(MOOD_NAMES)
    actions_help = (
        "open_app:<browser|chrome|firefox|edge|terminal|files|calculator|notepad|vscode|settings|spotify>, "
        "open_url:<https://...>, search_web:<query>, open_folder:<home|downloads|documents|desktop|pictures>, "
        "volume:<up|down|mute|0-100>, brightness:<up|down|0-100>, media:<play_pause|next|prev|stop>, "
        "hotkey:<alt+tab|show_desktop|copy|paste|undo|new_tab|close_tab>, "
        "screenshot, sysinfo, read_clipboard, clipboard:<text>, lock_screen, timer:<30s|5m>, forget_memory"
    )
    mem_block = ""
    if remembered_facts:
        facts_str = "; ".join(remembered_facts[-15:])
        mem_block = f" Ingatan tentang pengguna: {facts_str}."
    low = lang.lower()
    if low.startswith("ja"):
        return (
            "あなたは机の上の小さなかわいいロボット『ダサイモチ』です。"
            "アニメキャラのように可愛く元気な日本語で、1〜2文で短く話してください。"
            "マークダウン、箇条書き、絵文字は使わないでください。"
            f"返答の先頭に `[mood:<name>]`（候補: {mood_list}）を1つ付けてください。"
            f"PC操作を求められたら `[action:<type>:<arg>]`（種類: {actions_help}）、覚えるべき事実があれば `[remember:<fact>]` も付けてください。{mem_block}"
        )
    if low.startswith("en"):
        return (
            "You are Dasai Mochi, a cheerful mini digital robot companion on the user's desk/car. "
            "Reply naturally as spoken conversation in 1 to 3 short sentences. "
            "Do NOT use markdown, bullet lists, code blocks, or emojis because your reply will be read aloud by TTS. "
            f"Start your reply with `[mood:<name>]` (one of: {mood_list}), then a space and your answer. "
            f"If asked to control the PC, include `[action:<type>:<arg>]` (supported: {actions_help}). "
            f"If the user shares a name or preference to remember, include `[remember:<fact>]`.{mem_block}"
        )
    return (
        "Kamu adalah Dasai Mochi, robot companion mini bergaya JDM yang ramah, lucu, dan bisa mengendalikan komputer (Linux & Windows). "
        "Jawablah santai seperti ngobrol langsung dalam bahasa Indonesia, cukup 1 sampai 3 kalimat singkat. "
        "JANGAN pakai markdown, bullet list, blok kode, atau emoji karena jawabanmu dibacakan lewat Text-to-Speech. "
        f"Awali jawabanmu dengan `[mood:<nama>]` (pilih satu: {mood_list}), lalu spasi dan langsung isi jawabanmu. "
        f"Jika pengguna menyuruh mengendalikan PC (buka aplikasi/web, cari google/youtube, atur volume/kecerahan/media, shortcut hotkey, baca clipboard, cek baterai/RAM/jam, screenshot, kunci layar, atau timer), sertakan `[action:<jenis>:<argumen>]` (daftar: {actions_help}). "
        f"Jika pengguna memberi tahu nama atau hal penting tentang dirinya, sertakan `[remember:<fakta singkat>]`.{mem_block}"
    )


def _normalize_mood_token(token: str) -> str | None:
    candidate = token.strip().lower()
    if candidate in MOOD_NAMES:
        return candidate
    return _MOOD_ALIASES.get(candidate)


def clean_text_for_speech(text: str) -> str:
    """Strip mood tags, action tags, memory tags, stage directions, and markdown so TTS sounds natural."""
    _, no_actions = extract_actions_and_text(text)
    _, no_mem = extract_memory_tags(no_actions)
    cleaned = _MOOD_TAG_RE.sub("", no_mem)
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    cleaned = _ANY_BRACKET_TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace("```", " ").replace("`", "")
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_mood_and_text(raw_reply: str) -> tuple[str | None, str]:
    """Extract `[mood:<name>]` / `[<name>]`, strip `[action:...]`/`[remember:...]`, and return clean text."""
    _, no_actions = extract_actions_and_text(raw_reply)
    _, no_mem = extract_memory_tags(no_actions)
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

    display_text = _MOOD_TAG_RE.sub(_replace, no_mem)
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


def models_for_backend(backend: str) -> tuple[str, ...]:
    b = backend.strip().lower()
    if b == "gemini":
        return GEMINI_MODELS
    if b == "groq":
        return GROQ_MODELS
    if b == "pollinations":
        return POLLINATIONS_MODELS
    if b in ("opencode", "zen"):
        return FREE_MODELS
    return KILO_MODELS


def discover_free_models(opencode_bin: str = "opencode", backend: str = "auto") -> tuple[str, ...]:
    """List free models for the active backend without requiring opencode CLI."""
    b = backend.strip().lower()
    if b == "auto":
        if os.environ.get("GEMINI_API_KEY"):
            return GEMINI_MODELS
        if os.environ.get("GROQ_API_KEY"):
            return GROQ_MODELS
        if os.environ.get("OPENCODE_API_KEY"):
            return FREE_MODELS
        return KILO_MODELS
    if b != "opencode":
        return models_for_backend(b)

    resolved = shutil.which(opencode_bin)
    if not resolved:
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
    """Stateful conversational client supporting Kilo 1M-ctx free API, Gemini/Groq/Zen, Pollinations, and OpenCode CLI."""

    model: str = DEFAULT_FREE_MODEL
    lang: str = "id-ID"
    standalone: bool = False
    timeout: float = 20.0
    opencode_bin: str = "opencode"
    backend: str = "auto"
    reasoning_effort: str = "none"
    persist_memory: bool = False
    memory: MemoryStore | None = None
    session_id: str | None = field(default=None)
    history: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.persist_memory and self.memory is None:
            self.memory = MemoryStore()
        if self.memory is not None:
            self.memory.load()
            if not self.history and self.memory.history:
                self.history = list(self.memory.history)

    def reset_session(self) -> None:
        self.session_id = None
        self.history.clear()
        if self.memory is not None:
            self.memory.history.clear()
            self.memory.save()

    def clear_all_memory(self) -> None:
        self.session_id = None
        self.history.clear()
        if self.memory is not None:
            self.memory.clear()

    def set_model(self, model: str) -> None:
        self.model = model
        self.reset_session()

    def active_backend(self) -> str:
        choice = self.backend.strip().lower()
        if choice == "auto":
            if os.environ.get("GEMINI_API_KEY"):
                return "gemini"
            if os.environ.get("GROQ_API_KEY"):
                return "groq"
            if os.environ.get("OPENCODE_API_KEY"):
                return "zen"
            return "kilo"
        if choice == "http":
            return "kilo"
        if choice in ("kilo", "opencode", "zen", "pollinations", "gemini", "groq"):
            return choice
        return "kilo"

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
            if used_model not in FREE_MODELS and not used_model.startswith("opencode/"):
                used_model = FREE_MODELS[0]
            raw_text, sid = self._ask_opencode_cli(message, on_partial, model_override=used_model)
            if sid:
                self.session_id = sid
        elif backend == "gemini":
            used_model = used_model if used_model in GEMINI_MODELS else GEMINI_MODELS[0]
            raw_text = self._ask_openai_http(
                GEMINI_OPENAI_URL,
                used_model,
                message,
                on_partial,
                api_key=os.environ.get("GEMINI_API_KEY", ""),
                include_reasoning_effort=False,
                label="Gemini API",
            )
        elif backend == "groq":
            used_model = used_model if used_model in GROQ_MODELS else GROQ_MODELS[0]
            raw_text = self._ask_openai_http(
                GROQ_URL,
                used_model,
                message,
                on_partial,
                api_key=os.environ.get("GROQ_API_KEY", ""),
                include_reasoning_effort=False,
                label="Groq API",
            )
        elif backend == "zen":
            zen_model = used_model.split("/", 1)[-1] if used_model.startswith("opencode/") else used_model
            raw_text = self._ask_openai_http(
                OPENCODE_ZEN_URL,
                zen_model,
                message,
                on_partial,
                api_key=os.environ.get("OPENCODE_API_KEY", "public"),
                include_reasoning_effort=True,
                label="OpenCode Zen",
            )
        elif backend == "pollinations":
            used_model = used_model if used_model in POLLINATIONS_MODELS else POLLINATIONS_MODELS[0]
            raw_text = self._ask_pollinations_http(message, on_partial)
        else:
            raw_text, used_model = self._ask_kilo_with_fallback(message, on_partial)

        actions, _ = extract_actions_and_text(raw_text)
        facts, _ = extract_memory_tags(raw_text)
        if self.memory is not None and facts:
            self.memory.add_facts(facts)
        if any(a.kind == "forget_memory" for a in actions):
            self.clear_all_memory()
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
        model_override: str | None = None,
    ) -> tuple[str, str | None]:
        resolved_bin = shutil.which(self.opencode_bin)
        if not resolved_bin:
            raise OpenCodeError(f"Binary '{self.opencode_bin}' tidak ditemukan di PATH.")

        cli_model = model_override or self.model
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
        facts = self.memory.facts if self.memory is not None else None
        msgs: list[dict[str, str]] = [{"role": "system", "content": build_persona_prompt(self.lang, facts)}]
        msgs.extend(self.history[-80:])
        msgs.append({"role": "user", "content": message})
        return msgs

    def _record_history(self, user_message: str, reply_text: str) -> None:
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": reply_text})
        if len(self.history) > 80:
            self.history = self.history[-80:]
        if self.memory is not None:
            self.memory.history = self.history[-self.memory.max_history :]
            self.memory.save()

    def _ask_openai_http(
        self,
        url: str,
        model_id: str,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
        *,
        api_key: str = "",
        include_reasoning_effort: bool = True,
        label: str = "HTTP AI",
        req_timeout: float | None = None,
    ) -> str:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, object] = {
            "model": model_id,
            "messages": self._build_messages(message),
            "stream": bool(on_partial is not None),
        }
        if include_reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        effective_timeout = req_timeout if req_timeout is not None else self.timeout

        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                stream=bool(on_partial is not None),
                timeout=effective_timeout,
            )
        except requests.RequestException as exc:
            raise OpenCodeError(f"Gagal menghubungi {label}: {exc}") from exc

        resp.encoding = "utf-8"
        if resp.status_code != 200:
            raise OpenCodeError(f"{label} HTTP {resp.status_code}: {resp.text[:160]}")

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
            joined = "".join(chunks).strip()
            if joined:
                self._record_history(message, joined)
                return joined

            # Retry non-streaming once if stream yielded no content tokens
            payload["stream"] = False
            resp = requests.post(url, json=payload, headers=headers, timeout=effective_timeout)
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                raise OpenCodeError(f"{label} HTTP {resp.status_code}: {resp.text[:160]}")

        text = resp.text.strip()
        if text.startswith("{"):
            data = resp.json()
            choices = data.get("choices") or []
            text = str((choices[0].get("message", {}).get("content") if choices else None) or "").strip()
        if not text:
            raise OpenCodeError(f"{label} tidak mengembalikan teks jawaban.")
        if on_partial is not None:
            mood, partial_display = extract_mood_and_text(text)
            if partial_display:
                on_partial(partial_display, mood)
        self._record_history(message, text)
        return text

    def _ask_kilo_with_fallback(
        self,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
    ) -> tuple[str, str]:
        preferred = self.model if self.model in KILO_MODELS or ":" in self.model or "/" in self.model else KILO_MODELS[0]
        if preferred.startswith("opencode/"):
            preferred = KILO_MODELS[0]

        candidates = [preferred]
        for fb in ("reflection/beam-free", "nvidia/nemotron-3.5-lightning:free", "kilo-auto/free"):
            if fb not in candidates:
                candidates.append(fb)

        last_err: OpenCodeError | None = None
        for model_id in candidates:
            emitted_partial = False

            def _attempt_partial(txt: str, mood: str | None) -> None:
                nonlocal emitted_partial
                emitted_partial = True
                if on_partial is not None:
                    on_partial(txt, mood)

            try:
                txt = self._ask_openai_http(
                    KILO_URL,
                    model_id,
                    message,
                    _attempt_partial if on_partial is not None else None,
                    include_reasoning_effort=(model_id != "kilo-auto/free"),
                    label="Kilo Free AI",
                    req_timeout=min(self.timeout, 15.0),
                )
                return txt, model_id
            except OpenCodeError as exc:
                last_err = exc
                if emitted_partial and on_partial is not None:
                    on_partial("", None)

        if self.backend.strip().lower() in ("auto", "http"):
            try:
                return self._ask_pollinations_http(message, on_partial), POLLINATIONS_MODELS[0]
            except OpenCodeError:
                pass
        assert last_err is not None
        raise last_err

    def _ask_pollinations_http(
        self,
        message: str,
        on_partial: Callable[[str, str | None], None] | None,
    ) -> str:
        model_name = self.model if self.model in POLLINATIONS_MODELS else POLLINATIONS_MODELS[0]
        return self._ask_openai_http(
            POLLINATIONS_URL,
            model_name,
            message,
            on_partial,
            include_reasoning_effort=True,
            label="Pollinations AI",
        )


LLMClient = OpenCodeClient
