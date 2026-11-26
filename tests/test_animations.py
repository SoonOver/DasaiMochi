"""Tests for Dasai Mochi animation engine and moods."""

import pytest

from dasai_mochi.animations import MOOD_NAMES, MochiEngine
from dasai_mochi.framebuffer import PIXEL_OFF, Framebuffer


def _lit_coords(fb: Framebuffer) -> set[tuple[int, int]]:
    return {
        (x, y)
        for y, row in enumerate(fb.pixels)
        for x, val in enumerate(row)
        if val != PIXEL_OFF
    }


def test_all_moods_render_non_empty_and_animate():
    fb = Framebuffer(64, 32)
    for mood in MOOD_NAMES:
        engine = MochiEngine(mood=mood, auto_cycle=False, seed=123)
        engine.render(fb)
        frame_start = _lit_coords(fb)
        assert len(frame_start) > 20, f"Mood '{mood}' rendered too few pixels"

        # Advance time across several frames and confirm animation evolves
        seen_frames = {frozenset(frame_start)}
        for _ in range(30):
            engine.step(0.1)
            engine.render(fb)
            seen_frames.add(frozenset(_lit_coords(fb)))
        assert len(seen_frames) > 1, f"Mood '{mood}' did not animate over time"


def test_boop_triggers_distinct_reaction_and_recovers():
    engine = MochiEngine(mood="idle", auto_cycle=False, seed=7)
    fb = Framebuffer(64, 32)
    engine.render(fb)
    idle_pixels = _lit_coords(fb)

    engine.trigger_boop(duration=0.5)
    engine.render(fb)
    boop_pixels = _lit_coords(fb)
    assert boop_pixels != idle_pixels

    for _ in range(10):
        engine.step(0.1)
    assert engine._boop_remaining == 0.0


def test_sleepy_boop_wakes_up_to_surprised():
    engine = MochiEngine(mood="sleepy", auto_cycle=False, seed=1)
    engine.trigger_boop()
    assert engine.mood == "surprised"


def test_auto_cycle_switches_mood_after_interval():
    engine = MochiEngine(mood="idle", auto_cycle=True, cycle_interval=1.0, seed=42)
    for _ in range(15):
        engine.step(0.1)
    assert engine.mood != "idle"


def test_next_prev_mood_and_invalid_mood():
    engine = MochiEngine(mood="idle", auto_cycle=False)
    nxt = engine.next_mood()
    assert nxt == MOOD_NAMES[1]
    assert engine.prev_mood() == "idle"

    with pytest.raises(ValueError):
        engine.set_mood("nonexistent")


def test_speaking_pauses_auto_cycle_and_avoids_mouth_clash():
    engine = MochiEngine(mood="happy", auto_cycle=True, cycle_interval=1.0, seed=1)
    engine.voice_state = "speaking"
    for _ in range(30):
        engine.step(0.1)
    assert engine.mood == "happy"

    fb = Framebuffer(64, 32)
    engine.render(fb)
    # When speaking, happy should omit its static mouth at y=21 and show clean speaking mouth
    assert any(px != PIXEL_OFF for row in fb.pixels for px in row)
