"""Tests for CLI options and snapshot renderer."""

import pytest

from dasai_mochi.animations import MOOD_NAMES
from dasai_mochi.cli import main, render_snapshot
from dasai_mochi.themes import THEME_ORDER, get_theme


def test_snapshot_renders_without_ansi():
    out = render_snapshot(mood="happy", theme_name="amber", use_ascii=False, show_bezel=True, use_color=False)
    lines = out.splitlines()
    assert len(lines) == 21  # 16 OLED rows + 5 bezel lines
    assert "DASAI MOCHI" in lines[-1]
    assert all(len(line) == 72 for line in lines)


def test_snapshot_ascii_no_bezel():
    out = render_snapshot(mood="cool", theme_name="cyan", use_ascii=True, show_bezel=False, use_color=False)
    lines = out.splitlines()
    assert len(lines) == 16
    assert all(len(line) == 64 for line in lines)
    assert any("#" in line for line in lines)


def test_cli_list_moods_and_snapshot(capsys):
    assert main(["--list-moods"]) == 0
    captured = capsys.readouterr()
    for mood in MOOD_NAMES:
        assert mood in captured.out

    assert main(["--snapshot", "--no-ansi", "--mood", "love"]) == 0
    snap = capsys.readouterr().out
    assert "DASAI MOCHI" in snap


def test_theme_lookup():
    for name in THEME_ORDER:
        assert get_theme(name).name == name
    with pytest.raises(ValueError):
        get_theme("unknown")


def test_cli_list_voices_and_models(capsys, monkeypatch):
    from dasai_mochi.llm import DEFAULT_FREE_MODEL

    assert DEFAULT_FREE_MODEL == "opencode/beam-free"
    monkeypatch.setattr("dasai_mochi.cli.discover_free_models", lambda: ("opencode/beam-free",))
    assert main(["--list-models"]) == 0
    assert "opencode/beam-free" in capsys.readouterr().out

    assert main(["--list-voices"]) == 0
    voices_out = capsys.readouterr().out
    assert "tiktok-id" in voices_out
    assert "zunda-jp" in voices_out
    assert "zundamon-sweet" in voices_out


def test_cli_ask_mocked(capsys, monkeypatch):
    from dasai_mochi.llm import OpenCodeReply

    monkeypatch.setattr(
        "dasai_mochi.cli.OpenCodeClient.ask",
        lambda self, msg: OpenCodeReply(
            text="Halo bos!",
            spoken_text="Halo bos!",
            mood="happy",
            session_id="ses_1",
            model=self.model,
        ),
    )
    assert main(["--ask", "hai", "--no-tts"]) == 0
    assert "[happy] Halo bos!" in capsys.readouterr().out


def test_requirements_txt_matches_pyproject():
    from pathlib import Path
    import tomllib

    root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    reqs = (root / "requirements.txt").read_text(encoding="utf-8")
    for dep in pyproject["project"]["dependencies"]:
        pkg_name = dep.split(">=")[0].split("==")[0].strip().lower()
        assert pkg_name in reqs.lower()
