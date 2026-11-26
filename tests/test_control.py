"""Tests for cross-platform PC control actions."""

from types import SimpleNamespace

from dasai_mochi.control import ActionSpec, execute_action, extract_actions_and_text, get_system_summary


def test_extract_actions_and_text():
    raw = "[mood:happy] Oke, aku bukain kalkulator ya! [action:open_app:calculator]"
    actions, clean = extract_actions_and_text(raw)
    assert actions == [ActionSpec(kind="open_app", arg="calculator")]
    assert clean == "[mood:happy] Oke, aku bukain kalkulator ya!"


def test_open_url_and_search_validation(monkeypatch):
    opened = []
    monkeypatch.setattr("dasai_mochi.control.webbrowser.open", lambda url: opened.append(url) or True)

    res_ok = execute_action(ActionSpec("open_url", "https://youtube.com"))
    assert res_ok.ok is True
    assert opened[-1] == "https://youtube.com"

    res_bad = execute_action(ActionSpec("open_url", "file:///etc/passwd"))
    assert res_bad.ok is False

    res_yt = execute_action(ActionSpec("search_web", "youtube dasai mochi"))
    assert res_yt.ok is True
    assert "youtube.com/results" in opened[-1]


def test_sysinfo_and_timer_and_volume_mocked(monkeypatch):
    summary = get_system_summary()
    assert "CPU" in summary
    assert "Disk sisa" in summary

    res_sys = execute_action(ActionSpec("sysinfo"))
    assert res_sys.ok is True
    assert "CPU" in res_sys.message

    calls = []
    monkeypatch.setattr("dasai_mochi.control._is_windows", lambda: False)
    monkeypatch.setattr(
        "dasai_mochi.control.shutil.which",
        lambda name: "/usr/bin/wpctl" if name == "wpctl" else None,
    )
    monkeypatch.setattr(
        "dasai_mochi.control.subprocess.run",
        lambda cmd, **_kw: calls.append(cmd) or SimpleNamespace(returncode=0),
    )
    res_vol = execute_action(ActionSpec("volume", "up"))
    assert res_vol.ok is True
    assert calls[0][0] == "wpctl"

    bad_timer = execute_action(ActionSpec("timer", "invalid"))
    assert bad_timer.ok is False

    bad_app = execute_action(ActionSpec("open_app", "calc; rm -rf /"))
    assert bad_app.ok is False


def test_brightness_hotkey_clipboard_and_windows_screenshot_env(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("dasai_mochi.control._is_windows", lambda: False)
    monkeypatch.setattr(
        "dasai_mochi.control.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in ("brightnessctl", "xdotool", "wl-paste") else None,
    )

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        if cmd[0] == "wl-paste":
            return SimpleNamespace(returncode=0, stdout="Teks penting dari clipboard")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("dasai_mochi.control.subprocess.run", fake_run)

    res_br = execute_action(ActionSpec("brightness", "up"))
    assert res_br.ok is True
    assert calls[0][0] == "brightnessctl"

    res_hk = execute_action(ActionSpec("hotkey", "alt+tab"))
    assert res_hk.ok is True
    assert calls[1][0] == "xdotool"

    res_bad_hk = execute_action(ActionSpec("hotkey", "ctrl+alt+del"))
    assert res_bad_hk.ok is False

    res_cb = execute_action(ActionSpec("read_clipboard"))
    assert res_cb.ok is True
    assert "Teks penting" in res_cb.message

    # Windows screenshot uses MOCHI_SHOT env var (safe for quotes in path)
    win_calls = []
    monkeypatch.setattr("dasai_mochi.control._is_windows", lambda: True)
    monkeypatch.setattr("dasai_mochi.control.Path.home", lambda: tmp_path)

    def fake_win_shot(cmd, env=None, **_kw):
        from pathlib import Path

        win_calls.append((cmd, env))
        shot_path = Path(env["MOCHI_SHOT"])
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        shot_path.write_bytes(b"PNG")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dasai_mochi.control.subprocess.run", fake_win_shot)
    res_shot = execute_action(ActionSpec("screenshot"))
    assert res_shot.ok is True
    assert "$env:MOCHI_SHOT" in win_calls[0][0][-1]
