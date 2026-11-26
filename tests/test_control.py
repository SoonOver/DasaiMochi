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
    monkeypatch.setattr("dasai_mochi.control.os.name", "posix")
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
