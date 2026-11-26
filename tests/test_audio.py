"""Tests for audio helpers, voice presets, and voice animation states."""

import struct
import wave

from dasai_mochi.animations import MochiEngine
from dasai_mochi.audio import (
    DEFAULT_VOICE_KEY,
    VOICE_PRESETS,
    _fetch_voicevox_stream_url,
    _synthesize_tiktok_bytes,
    advance_subtitle_cursor,
    detect_recorder_commands,
    next_voice_preset,
    pcm16_rms,
    resolve_voice,
    split_tts_chunks,
    write_wav_file,
)
from dasai_mochi.framebuffer import PIXEL_OFF, Framebuffer


def test_voice_preset_resolution_and_cycling():
    assert DEFAULT_VOICE_KEY == "tiktok-id"
    vp = resolve_voice("tiktok-id")
    assert vp.backend == "tiktok"
    assert vp.voice_id == "id_001"
    assert vp.lang == "id-ID"
    assert vp.playback_speed == 1.18

    zunda = resolve_voice("zunda-jp")
    assert zunda.backend == "voicevox"
    assert zunda.speaker_id == 1
    assert zunda.lang == "ja-JP"

    nxt = next_voice_preset(vp)
    assert nxt == VOICE_PRESETS[1]

    custom = resolve_voice("jp_003", speed_override=1.2)
    assert custom.backend == "tiktok"
    assert custom.lang == "ja-JP"
    assert custom.playback_speed == 1.2


def test_split_tts_chunks_and_mocked_non_edge_backends(monkeypatch):
    chunks = split_tts_chunks("Kalimat satu! Kalimat dua sangat panjang sekali " * 8, max_chars=80)
    assert len(chunks) > 1
    assert all(len(c) <= 80 for c in chunks)

    import base64
    from types import SimpleNamespace

    monkeypatch.setattr(
        "dasai_mochi.audio.requests.post",
        lambda *_a, **_kw: SimpleNamespace(
            status_code=200,
            json=lambda: {"success": True, "data": base64.b64encode(b"FAKE_MP3").decode()},
        ),
    )
    assert _synthesize_tiktok_bytes("Halo Mochi", "id_001") == b"FAKE_MP3"

    monkeypatch.setattr(
        "dasai_mochi.audio.requests.get",
        lambda *_a, **_kw: SimpleNamespace(
            status_code=200,
            json=lambda: {"success": True, "mp3StreamingUrl": "https://audio1.tts.quest/test.mp3s"},
        ),
    )
    assert _fetch_voicevox_stream_url("こんにちは", 1) == "https://audio1.tts.quest/test.mp3s"


def test_windows_audio_dispatch_mocked(monkeypatch, tmp_path):
    import dasai_mochi.audio as audio_mod

    called = []
    monkeypatch.setattr(audio_mod, "_is_windows", lambda: True)
    monkeypatch.setattr(audio_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        audio_mod,
        "_play_audio_winmm",
        lambda path, **_kw: called.append(("play", path)),
    )
    mp3_file = tmp_path / "voice.mp3"
    mp3_file.write_bytes(b"MP3")
    audio_mod._play_audio_file(str(mp3_file), display_text="Halo")
    assert called == [("play", str(mp3_file))]

    monkeypatch.setattr(
        audio_mod,
        "_record_with_sounddevice",
        lambda wav_path, **_kw: 1.5,
    )
    assert audio_mod.record_mic_to_wav(str(tmp_path / "mic.wav")) == 1.5


def test_advance_subtitle_cursor_preserves_punctuation():
    txt = "Halo! Aku Mochi, robot mini."
    c1 = advance_subtitle_cursor(txt, 0, "Halo")
    assert txt[:c1] == "Halo!"
    c2 = advance_subtitle_cursor(txt, c1, "Aku")
    assert txt[:c2] == "Halo! Aku"
    c3 = advance_subtitle_cursor(txt, c2, "Mochi")
    assert txt[:c3] == "Halo! Aku Mochi,"


def test_pcm16_rms_and_wav_writer(tmp_path):
    silence = b"\x00\x00" * 100
    assert pcm16_rms(silence) == 0.0

    tone = struct.pack("<100h", *([16384] * 100))
    rms = pcm16_rms(tone)
    assert 0.49 < rms < 0.51

    wav_file = tmp_path / "sample.wav"
    write_wav_file(str(wav_file), tone, sample_rate=16000)
    with wave.open(str(wav_file), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 100


def test_detect_recorder_commands_runs(monkeypatch):
    host_cmds = detect_recorder_commands()
    assert isinstance(host_cmds, list)
    assert all(isinstance(name, str) and isinstance(cmd, list) and cmd for name, cmd in host_cmds)

    monkeypatch.setattr(
        "dasai_mochi.audio.shutil.which",
        lambda name: "/usr/bin/arecord" if name == "arecord" else None,
    )
    assert [name for name, _cmd in detect_recorder_commands()] == ["arecord"]

    monkeypatch.setattr("dasai_mochi.audio.shutil.which", lambda _name: None)
    assert detect_recorder_commands() == []


def test_voice_animation_states_render():
    fb = Framebuffer(64, 32)
    engine = MochiEngine(mood="happy", auto_cycle=False)

    for state in ("listening", "thinking", "speaking"):
        engine.voice_state = state
        engine.mic_level = 0.7
        engine.step(0.1)
        engine.render(fb)
        lit = sum(1 for row in fb.pixels for px in row if px != PIXEL_OFF)
        assert lit > 20, f"Voice state '{state}' rendered too few pixels"
