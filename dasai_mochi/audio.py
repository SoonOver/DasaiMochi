"""Microphone recording, Speech-to-Text (STT), and lightweight fast TTS pipeline."""

from __future__ import annotations

import asyncio
import base64
import math
import os
import re
import select
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass

import requests


TIKTOK_TTS_URL = "https://tiktok-tts.weilnet.workers.dev/api/generation"
VOICEVOX_QUEST_URL = "https://api.tts.quest/v3/voicevox/synthesis"


class AudioError(RuntimeError):
    """Raised when microphone recording, STT, or TTS fails."""


@dataclass(frozen=True)
class VoicePreset:
    key: str
    label: str
    backend: str
    voice_id: str
    lang: str
    gtts_lang: str
    speaker_id: int | None = None
    pitch: str = "+0Hz"
    rate: str = "+0%"
    playback_speed: float = 1.0

    @property
    def edge_voice(self) -> str:
        return self.voice_id


VOICE_PRESETS: tuple[VoicePreset, ...] = (
    VoicePreset(
        "tiktok-id",
        "TikTok Mochi ID",
        "tiktok",
        "id_001",
        "id-ID",
        "id",
        playback_speed=1.18,
    ),
    VoicePreset(
        "zunda-jp",
        "Zundamon Sweet",
        "voicevox",
        "zundamon-sweet",
        "ja-JP",
        "ja",
        speaker_id=1,
        playback_speed=1.06,
    ),
    VoicePreset(
        "zunda-norm",
        "Zundamon JP",
        "voicevox",
        "zundamon",
        "ja-JP",
        "ja",
        speaker_id=3,
        playback_speed=1.06,
    ),
    VoicePreset(
        "tiktok-anime",
        "TikTok Anime JP",
        "tiktok",
        "jp_003",
        "ja-JP",
        "ja",
        playback_speed=1.12,
    ),
    VoicePreset(
        "tiktok-jp",
        "TikTok Miho JP",
        "tiktok",
        "jp_001",
        "ja-JP",
        "ja",
        playback_speed=1.12,
    ),
    VoicePreset(
        "tiktok-en",
        "TikTok Cute EN",
        "tiktok",
        "en_us_001",
        "en-US",
        "en",
        playback_speed=1.14,
    ),
    VoicePreset(
        "gtts-chibi",
        "gTTS Chibi ID",
        "gtts",
        "gtts-id",
        "id-ID",
        "id",
        playback_speed=1.22,
    ),
    VoicePreset(
        "edge-chibi",
        "Edge Chibi ID",
        "edge",
        "id-ID-GadisNeural",
        "id-ID",
        "id",
        pitch="+16Hz",
        rate="+4%",
        playback_speed=1.18,
    ),
    VoicePreset(
        "edge-cute",
        "Edge Ana EN",
        "edge",
        "en-US-AnaNeural",
        "en-US",
        "en",
        pitch="+8Hz",
        rate="+0%",
        playback_speed=1.12,
    ),
)

DEFAULT_VOICE_KEY = VOICE_PRESETS[0].key
VOICE_BY_KEY: dict[str, VoicePreset] = {v.key: v for v in VOICE_PRESETS}


def resolve_voice(
    voice_arg: str,
    lang_override: str | None = None,
    pitch_override: str | None = None,
    rate_override: str | None = None,
    speed_override: float | None = None,
) -> VoicePreset:
    """Resolve a preset key (`tiktok-id`, `zunda-jp`, etc.) or raw voice ID."""
    cleaned = voice_arg.strip()
    if cleaned in VOICE_BY_KEY:
        preset = VOICE_BY_KEY[cleaned]
        lang = lang_override or preset.lang
        return VoicePreset(
            key=preset.key,
            label=preset.label,
            backend=preset.backend,
            voice_id=preset.voice_id,
            lang=lang,
            gtts_lang=lang.split("-")[0].lower() if lang_override else preset.gtts_lang,
            speaker_id=preset.speaker_id,
            pitch=pitch_override or preset.pitch,
            rate=rate_override or preset.rate,
            playback_speed=speed_override if speed_override is not None else preset.playback_speed,
        )

    low = cleaned.lower()
    if low.startswith(("id_", "jp_", "en_", "es_", "fr_", "de_", "kr_", "br_")):
        backend = "tiktok"
        default_lang = "ja-JP" if low.startswith("jp_") else ("en-US" if low.startswith("en_") else "id-ID")
    elif low.startswith("voicevox:"):
        backend = "voicevox"
        default_lang = "ja-JP"
    else:
        backend = "edge"
        default_lang = "ja-JP" if low.startswith("ja-") else ("en-US" if low.startswith("en-") else "id-ID")

    lang = lang_override or default_lang
    spk = int(cleaned.split(":", 1)[1]) if backend == "voicevox" and cleaned.split(":", 1)[1].isdigit() else None
    return VoicePreset(
        key=cleaned,
        label=cleaned,
        backend=backend,
        voice_id=cleaned,
        lang=lang,
        gtts_lang=lang.split("-")[0].lower(),
        speaker_id=spk,
        pitch=pitch_override or "+0Hz",
        rate=rate_override or "+0%",
        playback_speed=speed_override if speed_override is not None else 1.0,
    )


def next_voice_preset(current: VoicePreset) -> VoicePreset:
    keys = [v.key for v in VOICE_PRESETS]
    if current.key in keys:
        idx = keys.index(current.key)
        return VOICE_PRESETS[(idx + 1) % len(VOICE_PRESETS)]
    return VOICE_PRESETS[0]


def detect_recorder_commands() -> list[tuple[str, list[str]]]:
    """Return available Linux raw 16kHz mono s16le microphone capture commands."""
    candidates: list[tuple[str, list[str]]] = [
        ("pw-record", ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--container", "raw", "-"]),
        ("parecord", ["parecord", "--raw", "--rate=16000", "--channels=1", "--format=s16le", "--latency-msec=50"]),
        ("arecord", ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "-"]),
        ("ffmpeg", ["ffmpeg", "-loglevel", "error", "-f", "pulse", "-i", "default", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"]),
    ]
    return [(name, cmd) for name, cmd in candidates if shutil.which(name)]


def pcm16_rms(pcm_bytes: bytes) -> float:
    """Compute normalized RMS amplitude (0.0..1.0) for 16-bit little-endian PCM bytes."""
    n_samples = len(pcm_bytes) // 2
    if n_samples == 0:
        return 0.0
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2])
    mean_sq = sum(s * s for s in samples) / n_samples
    return min(1.0, math.sqrt(mean_sq) / 32768.0)


def write_wav_file(path: str, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    """Write 16-bit mono PCM bytes to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def record_mic_to_wav(
    wav_path: str,
    stop_event: threading.Event | None = None,
    on_level: Callable[[float], None] | None = None,
    max_seconds: float = 15.0,
    silence_seconds: float = 1.2,
    min_speech_seconds: float = 0.3,
) -> float:
    """Record audio from the default microphone until silence or `stop_event`."""
    recorders = detect_recorder_commands()
    if not recorders:
        raise AudioError("Tidak ada tool perekam mic (pw-record / parecord / arecord / ffmpeg).")

    last_err = ""
    chunk_bytes = 1600
    sample_rate = 16000

    for name, cmd in recorders:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            last_err = f"{name}: {exc}"
            continue

        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        pcm_chunks: list[bytes] = []
        started_at = time.monotonic()
        noise_samples: list[float] = []
        speech_detected = False
        speech_seconds = 0.0
        silence_run = 0.0

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                if time.monotonic() - started_at >= max_seconds:
                    break

                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue

                data = os.read(fd, chunk_bytes)
                if not data:
                    break
                pcm_chunks.append(data)

                rms = pcm16_rms(data)
                if on_level is not None:
                    on_level(min(1.0, rms * 8.0))

                if len(noise_samples) < 7:
                    noise_samples.append(rms)
                    continue

                noise_floor = sum(noise_samples) / len(noise_samples)
                threshold = max(0.012, noise_floor * 2.4)
                chunk_dur = len(data) / (sample_rate * 2)

                if rms >= threshold:
                    speech_detected = True
                    speech_seconds += chunk_dur
                    silence_run = 0.0
                elif speech_detected:
                    silence_run += chunk_dur
                    if speech_seconds >= min_speech_seconds and silence_run >= silence_seconds:
                        break
        finally:
            if on_level is not None:
                on_level(0.0)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            stderr_msg = ""
            if proc.stderr is not None:
                try:
                    stderr_msg = proc.stderr.read().decode(errors="ignore").strip()
                except OSError:
                    pass

        pcm = b"".join(pcm_chunks)
        if len(pcm) >= 3200:
            write_wav_file(wav_path, pcm, sample_rate=sample_rate)
            return len(pcm) / (sample_rate * 2)

        last_err = f"{name}: {stderr_msg or 'tidak ada data audio dari mikrofon'}"

    raise AudioError(f"Gagal merekam mic ({last_err}).")


def transcribe_wav(wav_path: str, language: str = "id-ID") -> str:
    """Transcribe a WAV file to text using SpeechRecognition."""
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise AudioError("Paket 'SpeechRecognition' belum terpasang di environment Python.") from exc

    recognizer = sr.Recognizer()
    with sr.AudioFile(wav_path) as source:
        audio = recognizer.record(source)

    try:
        text = recognizer.recognize_google(audio, language=language)
    except sr.UnknownValueError as exc:
        raise AudioError("Suara tidak terdengar jelas, coba bicara lagi.") from exc
    except sr.RequestError as exc:
        raise AudioError(f"Layanan STT gagal diakses: {exc}") from exc

    cleaned = text.strip()
    if not cleaned:
        raise AudioError("Hasil transkripsi kosong.")
    return cleaned


def advance_subtitle_cursor(display_text: str, cursor: int, word_text: str) -> int:
    """Advance `cursor` inside `display_text` past `word_text` and trailing punctuation."""
    if cursor >= len(display_text):
        return len(display_text)
    token = word_text.strip()
    if not token:
        return cursor

    idx = display_text.lower().find(token.lower(), cursor)
    if idx != -1:
        end = idx + len(token)
        while end < len(display_text) and display_text[end] in ".,!?;:\"'…)]}":
            end += 1
        return end

    match = re.search(r"\S+\s*", display_text[cursor:])
    if match:
        return cursor + match.end()
    return len(display_text)


def split_tts_chunks(text: str, max_chars: int = 220) -> list[str]:
    """Split text cleanly along sentence/clause boundaries under `max_chars`."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    chunks: list[str] = []
    sentences = re.split(r"(?<=[.!?。！？])\s+", cleaned)
    current = ""
    for sent in sentences:
        if len(sent) > max_chars:
            words = sent.split(" ")
            for w in words:
                candidate = f"{current} {w}".strip() if current else w
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = w[:max_chars]
        else:
            candidate = f"{current} {sent}".strip() if current else sent
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sent
    if current:
        chunks.append(current)
    return chunks


def _build_mpv_cmd(target: str, speed: float, stream: bool = False) -> list[str]:
    cmd = ["mpv", "--no-video", "--really-quiet"]
    if stream:
        cmd.append("--cache=no")
    if abs(speed - 1.0) > 0.01:
        cmd.extend(["--audio-pitch-correction=no", f"--speed={speed:.2f}"])
    cmd.append(target)
    return cmd


def _play_target_with_pacing(
    cmd: list[str],
    display_text: str,
    speed: float,
    on_start: Callable[[], None] | None,
    on_word: Callable[[str], None] | None,
    cancel_event: threading.Event | None,
    stdin_bytes: bytes | None = None,
) -> int:
    """Run player `cmd` (optionally piping `stdin_bytes`) and pace word subtitles."""
    words = display_text.split() if display_text else []
    word_idx = 0

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_bytes is not None else None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if on_start is not None:
        on_start()
    started = time.monotonic()

    if stdin_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        if on_word is not None and words:
            elapsed = time.monotonic() - started
            target = min(len(words), max(1, int(elapsed * 4.2 * max(0.8, speed))))
            if target > word_idx:
                word_idx = target
                on_word(" ".join(words[:word_idx]))
        time.sleep(0.04)

    if on_word is not None and display_text and not (cancel_event and cancel_event.is_set()):
        on_word(display_text)
    return proc.returncode or 0


def _synthesize_tiktok_bytes(text: str, voice_id: str) -> bytes:
    """Synthesize MP3 bytes using TikTok TTS API."""
    audio_parts: list[bytes] = []
    for chunk in split_tts_chunks(text, max_chars=220):
        resp = requests.post(
            TIKTOK_TTS_URL,
            json={"text": chunk, "voice": voice_id},
            timeout=10,
        )
        if resp.status_code != 200:
            raise AudioError(f"TikTok TTS HTTP {resp.status_code}")
        payload = resp.json()
        if not payload.get("success") or not payload.get("data"):
            raise AudioError(payload.get("error") or "TikTok TTS gagal membuat audio.")
        audio_parts.append(base64.b64decode(payload["data"]))
    return b"".join(audio_parts)


def _fetch_voicevox_stream_url(text: str, speaker_id: int) -> str:
    """Request streaming MP3 URL from VOICEVOX quest endpoint."""
    resp = requests.get(
        VOICEVOX_QUEST_URL,
        params={"text": text[:200], "speaker": speaker_id},
        timeout=10,
    )
    if resp.status_code != 200:
        raise AudioError(f"VOICEVOX HTTP {resp.status_code}")
    payload = resp.json()
    if not payload.get("success"):
        raise AudioError(payload.get("errorMessage") or "VOICEVOX rate limit / gagal.")
    url = payload.get("mp3StreamingUrl") or payload.get("mp3DownloadUrl")
    if not url:
        raise AudioError("VOICEVOX tidak mengembalikan URL audio.")
    return str(url)


def _synthesize_gtts_to_mp3(text: str, lang_short: str, out_mp3: str) -> None:
    from gtts import gTTS

    tts = gTTS(text=text, lang=lang_short)
    tts.save(out_mp3)
    if not (os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0):
        raise AudioError("gTTS menghasilkan file kosong.")


def _synthesize_to_mp3(text: str, voice: VoicePreset, out_mp3: str) -> None:
    """Synthesize text to an MP3 file using the preset's backend with gTTS fallback."""
    err_notes: list[str] = []
    if voice.backend == "tiktok":
        try:
            mp3_bytes = _synthesize_tiktok_bytes(text, voice.voice_id)
            with open(out_mp3, "wb") as f:
                f.write(mp3_bytes)
            return
        except Exception as exc:
            err_notes.append(f"tiktok: {exc}")
    elif voice.backend == "edge":
        try:
            import edge_tts

            async def _save() -> None:
                comm = edge_tts.Communicate(
                    text=text,
                    voice=voice.voice_id,
                    pitch=voice.pitch,
                    rate=voice.rate,
                )
                await comm.save(out_mp3)

            asyncio.run(_save())
            if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0:
                return
        except Exception as exc:
            err_notes.append(f"edge: {exc}")

    try:
        _synthesize_gtts_to_mp3(text, voice.gtts_lang, out_mp3)
    except Exception as gtts_exc:
        detail = "; ".join([*err_notes, f"gTTS: {gtts_exc}"])
        raise AudioError(f"TTS gagal ({detail})") from gtts_exc


def _play_audio_file(
    mp3_path: str,
    speed: float = 1.0,
    display_text: str = "",
    on_start: Callable[[], None] | None = None,
    on_word: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Play an MP3 file using available Linux players with subtitle pacing."""
    wav_path: str | None = None
    cmd: list[str] | None = None

    if shutil.which("mpv"):
        cmd = _build_mpv_cmd(mp3_path, speed=speed, stream=False)
    elif shutil.which("ffplay"):
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", mp3_path]
    elif shutil.which("ffmpeg"):
        fd, wav_path = tempfile.mkstemp(prefix="mochi_tts_", suffix=".wav")
        os.close(fd)
        conv = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path, wav_path],
            capture_output=True,
            check=False,
        )
        if conv.returncode == 0:
            for player in ("pw-play", "paplay", "aplay"):
                if shutil.which(player):
                    cmd = [player, wav_path]
                    break

    if cmd is None:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)
        raise AudioError("Tidak ada audio player (mpv / ffplay / pw-play / paplay / aplay).")

    try:
        _play_target_with_pacing(
            cmd=cmd,
            display_text=display_text,
            speed=speed,
            on_start=on_start,
            on_word=on_word,
            cancel_event=cancel_event,
        )
    finally:
        if wav_path and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


def _stream_edge_to_mpv(
    spoken_text: str,
    display_text: str,
    voice: VoicePreset,
    on_start: Callable[[], None] | None,
    on_word: Callable[[str], None] | None,
    cancel_event: threading.Event | None,
) -> bool:
    """Stream edge-tts audio chunks directly to mpv stdin with WordBoundary subtitle sync."""
    if not shutil.which("mpv"):
        return False
    try:
        import edge_tts
    except ImportError:
        return False

    speed = max(0.7, min(1.6, voice.playback_speed))
    proc: subprocess.Popen[bytes] | None = None
    word_events: list[tuple[float, str]] = []
    playback_t0: float | None = None
    reveal_cursor = 0
    next_event_idx = 0

    def _pump_subtitles() -> None:
        nonlocal reveal_cursor, next_event_idx
        if on_word is None or playback_t0 is None or not display_text:
            return
        elapsed = time.monotonic() - playback_t0
        updated = False
        while next_event_idx < len(word_events):
            ev_time, ev_word = word_events[next_event_idx]
            if elapsed + 0.04 < ev_time:
                break
            reveal_cursor = advance_subtitle_cursor(display_text, reveal_cursor, ev_word)
            next_event_idx += 1
            updated = True
        if updated:
            on_word(display_text[:reveal_cursor])

    async def _run() -> bool:
        nonlocal proc, playback_t0
        comm = edge_tts.Communicate(
            text=spoken_text,
            voice=voice.voice_id,
            pitch=voice.pitch,
            rate=voice.rate,
            boundary="WordBoundary",
        )
        got_audio = False
        async for chunk in comm.stream():
            if cancel_event is not None and cancel_event.is_set():
                break
            ctype = chunk.get("type")
            if ctype == "audio":
                data = chunk.get("data") or b""
                if not data:
                    continue
                if proc is None:
                    proc = subprocess.Popen(
                        _build_mpv_cmd("-", speed=speed, stream=True),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    playback_t0 = time.monotonic()
                    if on_start is not None:
                        on_start()
                got_audio = True
                if proc.stdin is not None:
                    try:
                        proc.stdin.write(data)
                        proc.stdin.flush()
                    except BrokenPipeError:
                        break
            elif ctype == "WordBoundary":
                offset_sec = (chunk.get("offset", 0) / 10_000_000.0) / speed
                wtext = str(chunk.get("text", ""))
                if wtext:
                    word_events.append((offset_sec, wtext))
            _pump_subtitles()

        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        return got_audio

    try:
        streamed = asyncio.run(_run())
        if not streamed or proc is None:
            return False

        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            _pump_subtitles()
            time.sleep(0.04)

        if on_word is not None and display_text and not (cancel_event and cancel_event.is_set()):
            on_word(display_text)
        return True
    except Exception:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        return False


def speak_text(
    text: str,
    voice: VoicePreset,
    on_start: Callable[[], None] | None = None,
    on_end: Callable[[], None] | None = None,
    on_word: Callable[[str], None] | None = None,
    display_text: str | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Synthesize and stream `text` via TTS with word-level subtitle callbacks."""
    cleaned = text.strip()
    subtitle_target = (display_text or cleaned).strip()
    if not cleaned:
        return
    if cancel_event is not None and cancel_event.is_set():
        return

    has_mpv = shutil.which("mpv") is not None
    speed = max(0.7, min(1.6, voice.playback_speed))

    try:
        # 1. VOICEVOX anime character streaming URL
        if voice.backend == "voicevox" and has_mpv:
            try:
                stream_url = _fetch_voicevox_stream_url(cleaned, voice.speaker_id or 1)
                rc = _play_target_with_pacing(
                    cmd=_build_mpv_cmd(stream_url, speed=speed, stream=True),
                    display_text=subtitle_target,
                    speed=speed,
                    on_start=on_start,
                    on_word=on_word,
                    cancel_event=cancel_event,
                )
                if rc == 0 or (cancel_event is not None and cancel_event.is_set()):
                    return
            except Exception:
                # Fallback to TikTok cute Japanese voice if VOICEVOX rate-limits
                fallback = VOICE_BY_KEY["tiktok-anime"]
                try:
                    mp3_bytes = _synthesize_tiktok_bytes(cleaned, fallback.voice_id)
                    _play_target_with_pacing(
                        cmd=_build_mpv_cmd("-", speed=fallback.playback_speed, stream=True),
                        display_text=subtitle_target,
                        speed=fallback.playback_speed,
                        on_start=on_start,
                        on_word=on_word,
                        cancel_event=cancel_event,
                        stdin_bytes=mp3_bytes,
                    )
                    return
                except Exception:
                    pass

        # 2. TikTok TTS piped straight to mpv stdin (fast & zero temp file)
        if voice.backend == "tiktok" and has_mpv:
            try:
                mp3_bytes = _synthesize_tiktok_bytes(cleaned, voice.voice_id)
                _play_target_with_pacing(
                    cmd=_build_mpv_cmd("-", speed=speed, stream=True),
                    display_text=subtitle_target,
                    speed=speed,
                    on_start=on_start,
                    on_word=on_word,
                    cancel_event=cancel_event,
                    stdin_bytes=mp3_bytes,
                )
                return
            except Exception:
                pass

        # 3. Optional Edge TTS streaming with WordBoundary sync
        if voice.backend == "edge" and _stream_edge_to_mpv(
            spoken_text=cleaned,
            display_text=subtitle_target,
            voice=voice,
            on_start=on_start,
            on_word=on_word,
            cancel_event=cancel_event,
        ):
            return

        if cancel_event is not None and cancel_event.is_set():
            return

        # 4. General file fallback (TikTok/Edge/gTTS -> temp MP3 -> available player)
        fd, mp3_path = tempfile.mkstemp(prefix="mochi_tts_", suffix=".mp3")
        os.close(fd)
        try:
            _synthesize_to_mp3(cleaned, voice, mp3_path)
            if cancel_event is not None and cancel_event.is_set():
                return
            _play_audio_file(
                mp3_path,
                speed=speed,
                display_text=subtitle_target,
                on_start=on_start,
                on_word=on_word,
                cancel_event=cancel_event,
            )
        finally:
            if os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except OSError:
                    pass
    finally:
        if on_end is not None:
            on_end()
