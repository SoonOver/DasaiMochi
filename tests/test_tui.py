"""Tests for TUI VoiceController barge-in, stop, and hands-free controls."""

import threading

from dasai_mochi.animations import MochiEngine
from dasai_mochi.audio import resolve_voice
from dasai_mochi.llm import OpenCodeClient
from dasai_mochi.tui import VoiceController


def test_barge_in_interrupts_speaking_and_stop_resets():
    engine = MochiEngine(mood="idle", auto_cycle=False)
    llm = OpenCodeClient(backend="kilo")
    ctrl = VoiceController(engine=engine, llm=llm, voice=resolve_voice("tiktok-id"), tts_enabled=False)

    # Simulate an active speaking turn
    ctrl._busy = True
    ctrl.voice_state = "speaking"
    ctrl._worker_done = threading.Event()

    def _finish_soon():
        ctrl._cancel_event.wait(timeout=1.0)
        with ctrl.lock:
            ctrl.voice_state = "none"
            ctrl._busy = False
        ctrl._worker_done.set()

    t = threading.Thread(target=_finish_soon, daemon=True)
    t.start()

    assert ctrl.interrupt_speaking(wait_timeout=1.0) is True
    assert ctrl.is_busy() is False
    assert ctrl.voice_state == "none"

    ctrl.hands_free = True
    ctrl.stop_current_turn()
    assert ctrl.hands_free is False
    assert ctrl.status_line == "Dihentikan."
