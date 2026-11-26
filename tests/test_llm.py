"""Tests for OpenCode free-model client and response parsing."""

from types import SimpleNamespace

import pytest

from dasai_mochi.llm import (
    DEFAULT_FREE_MODEL,
    OpenCodeClient,
    OpenCodeError,
    clean_text_for_speech,
    extract_mood_and_text,
    parse_opencode_json_output,
)


def test_parse_opencode_json_output_and_session():
    ndjson = "\n".join([
        '{"type":"step_start","sessionID":"ses_abc123","part":{"type":"step-start"}}',
        '{"type":"text","sessionID":"ses_abc123","part":{"type":"text","text":"[mood:happy] Halo! "}}',
        '{"type":"text","sessionID":"ses_abc123","part":{"type":"text","text":"Aku Mochi."}}',
        '{"type":"step_finish","sessionID":"ses_abc123","part":{"type":"step-finish","cost":0}}',
    ])
    text, sid = parse_opencode_json_output(ndjson)
    assert sid == "ses_abc123"
    assert text == "[mood:happy] Halo! Aku Mochi."


def test_parse_opencode_error_event():
    ndjson = '{"type":"error","error":{"message":"Model unavailable"}}'
    with pytest.raises(OpenCodeError, match="Model unavailable"):
        parse_opencode_json_output(ndjson)


def test_extract_mood_and_clean_speech():
    mood, display = extract_mood_and_text("[mood:COOL] Halo **bro**, yuk jalan!")
    assert mood == "cool"
    assert display == "Halo **bro**, yuk jalan!"
    assert clean_text_for_speech(display) == "Halo bro, yuk jalan!"

    unknown_mood, clean_only = extract_mood_and_text("[mood:unknown] Hai!")
    assert unknown_mood is None
    assert clean_only == "Hai!"

    bare_mood, bare_display = extract_mood_and_text("[surprised] Wah beneran nih?!")
    assert bare_mood == "surprised"
    assert bare_display == "Wah beneran nih?!"
    assert clean_text_for_speech("[surprised] Wah [laughs] beneran nih?!") == "Wah beneran nih?!"

    spaced_mood, spaced_display = extract_mood_and_text("[mood: shocked] Kaget banget!")
    assert spaced_mood == "surprised"
    assert spaced_display == "Kaget banget!"

    partial_mood, partial_display = extract_mood_and_text("[surp")
    assert partial_mood is None
    assert partial_display == ""


def test_opencode_client_ask_tracks_session(monkeypatch):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"text","sessionID":"ses_999","part":{"type":"text","text":"[mood:love] Hai juga!"}}\n',
            stderr="",
        )

    monkeypatch.setattr("dasai_mochi.llm.shutil.which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr("dasai_mochi.llm.subprocess.run", fake_run)

    client = OpenCodeClient(model=DEFAULT_FREE_MODEL, lang="id-ID")
    r1 = client.ask("Halo Mochi")
    assert r1.mood == "love"
    assert r1.text == "Hai juga!"
    assert r1.session_id == "ses_999"
    assert "--session" not in calls[0]

    r2 = client.ask("Lagi ngapain?")
    assert r2.session_id == "ses_999"
    assert "--session" in calls[1]
    assert "ses_999" in calls[1]


def test_opencode_client_streaming_partial_callbacks(monkeypatch):
    import io

    class FakeProc:
        def __init__(self):
            self.stdout = io.StringIO(
                '{"type":"text","sessionID":"ses_s1","part":{"type":"text","text":"[mood:happy] Halo "}}\n'
                '{"type":"text","sessionID":"ses_s1","part":{"type":"text","text":"dunia!"}}\n'
            )
            self.stderr = io.StringIO("")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("dasai_mochi.llm.shutil.which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr("dasai_mochi.llm.subprocess.Popen", lambda *a, **kw: FakeProc())

    partials = []
    client = OpenCodeClient(model=DEFAULT_FREE_MODEL)
    reply = client.ask("Hai", on_partial=lambda txt, mood: partials.append((txt, mood)))
    assert reply.mood == "happy"
    assert reply.text == "Halo dunia!"
    assert partials == [("Halo", "happy"), ("Halo dunia!", "happy")]
