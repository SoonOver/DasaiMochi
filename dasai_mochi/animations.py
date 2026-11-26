"""Procedural expressions, animations, and state engine for Dasai Mochi."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .framebuffer import PIXEL_DIM, PIXEL_ON, Framebuffer


@dataclass(frozen=True)
class MoodInfo:
    name: str
    label: str
    key: str
    description: str


MOODS: tuple[MoodInfo, ...] = (
    MoodInfo("idle", "Idle", "1", "Calm blinking & curious glances"),
    MoodInfo("happy", "Happy", "2", "Joyful ^ ^ eyes & sparkles"),
    MoodInfo("love", "Love", "3", "Pulsing heart eyes & blush"),
    MoodInfo("sleepy", "Sleepy", "4", "Nodding off with floating Zzz"),
    MoodInfo("cool", "Cool", "5", "JDM shades with visor glint"),
    MoodInfo("surprised", "Surprised", "6", "Wide O O shock & alert!"),
    MoodInfo("angry", "Angry", "7", "Fierce > < glare & puff"),
    MoodInfo("vibe", "Vibe", "8", "Music beat & spectrum bars"),
    MoodInfo("turbo", "Turbo", "9", "Boost gauge & speed lines"),
    MoodInfo("glitch", "Glitch", "0", "Dizzy cyber static & scanlines"),
)

MOOD_NAMES: tuple[str, ...] = tuple(m.name for m in MOODS)
MOOD_BY_KEY: dict[str, str] = {m.key: m.name for m in MOODS}

HEART_BIG: tuple[str, ...] = (
    "  ###   ###  ",
    " ##### ##### ",
    "#############",
    "#############",
    "#############",
    " ########### ",
    "  #########  ",
    "   #######   ",
    "    #####    ",
    "     ###     ",
    "      #      ",
)

HEART_MINI: tuple[str, ...] = (
    " # # ",
    "#####",
    " ### ",
    "  #  ",
)

SPARKLE_BIG: tuple[str, ...] = (
    "  #  ",
    "  #  ",
    "#####",
    "  #  ",
    "  #  ",
)

NOTE_EIGHTH: tuple[str, ...] = (
    "  ## ",
    "  # #",
    "  #  ",
    " ### ",
    "#### ",
    " ### ",
)

NOTE_BEAMED: tuple[str, ...] = (
    "  ####",
    "  #  #",
    "  #  #",
    " ## ##",
    "### ##",
    " #  # ",
)

ANGER_MARK: tuple[str, ...] = (
    " #   # ",
    "  # #  ",
    "##   ##",
    "  # #  ",
    " #   # ",
)

Z_SMALL: tuple[str, ...] = (
    "###",
    "  #",
    " # ",
    "###",
)

Z_LARGE: tuple[str, ...] = (
    "#####",
    "   # ",
    "  #  ",
    " #   ",
    "#####",
)


def _lerp(current: float, target: float, factor: float) -> float:
    return current + (target - current) * min(1.0, max(0.0, factor))


@dataclass
class MochiEngine:
    """Stateful animation engine driving a 64x32 OLED framebuffer."""

    mood: str = "idle"
    auto_cycle: bool = True
    cycle_interval: float = 12.0
    seed: int | None = None

    elapsed: float = 0.0
    mood_elapsed: float = 0.0
    _rng: random.Random = field(init=False, repr=False)

    # Smooth eye gaze & blink state
    _gaze_x: float = 0.0
    _gaze_y: float = 0.0
    _target_gaze_x: float = 0.0
    _target_gaze_y: float = 0.0
    _next_gaze_at: float = 1.8

    _blink_progress: float = 0.0
    _blinking: bool = False
    _next_blink_at: float = 2.6

    _boop_remaining: float = 0.0
    _particles_offset: float = 0.0

    # Voice assistant visual overlay state: "none", "listening", "thinking", "speaking"
    voice_state: str = "none"
    mic_level: float = 0.0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.set_mood(self.mood, reset_timer=True)

    def set_mood(self, mood: str, reset_timer: bool = True) -> None:
        key = mood.strip().lower()
        if key not in MOOD_NAMES:
            valid = ", ".join(MOOD_NAMES)
            raise ValueError(f"Unknown mood '{mood}'. Choose from: {valid}")
        self.mood = key
        if reset_timer:
            self.mood_elapsed = 0.0
        self._target_gaze_x = 0.0
        self._target_gaze_y = 0.0

    def next_mood(self) -> str:
        idx = MOOD_NAMES.index(self.mood)
        nxt = MOOD_NAMES[(idx + 1) % len(MOOD_NAMES)]
        self.set_mood(nxt)
        return nxt

    def prev_mood(self) -> str:
        idx = MOOD_NAMES.index(self.mood)
        prv = MOOD_NAMES[(idx - 1) % len(MOOD_NAMES)]
        self.set_mood(prv)
        return prv

    def trigger_boop(self, duration: float = 1.8) -> None:
        """Trigger a cute head-pat / boop reaction."""
        self._boop_remaining = max(self._boop_remaining, duration)
        if self.mood == "sleepy":
            self.set_mood("surprised")

    def step(self, dt: float) -> None:
        """Advance simulation state by `dt` seconds."""
        dt = max(0.0, min(dt, 0.25))
        self.elapsed += dt
        self._particles_offset += dt

        if self._boop_remaining > 0.0:
            self._boop_remaining = max(0.0, self._boop_remaining - dt)

        # Pause auto-mood cycling while interacting via voice so speaking and random moods never clash
        if self.voice_state == "none" and self._boop_remaining <= 0.0:
            self.mood_elapsed += dt
            if self.auto_cycle and self.mood_elapsed >= self.cycle_interval:
                candidates = [m for m in MOOD_NAMES if m != self.mood]
                self.set_mood(self._rng.choice(candidates))

        # Center gaze smoothly when listening/speaking; allow random glances only when idle/free
        if self.voice_state != "none":
            self._target_gaze_x = 0.0
            self._target_gaze_y = 0.0
        elif self.elapsed >= self._next_gaze_at:
            self._next_gaze_at = self.elapsed + self._rng.uniform(1.4, 3.6)
            if self._rng.random() < 0.35:
                self._target_gaze_x = 0.0
                self._target_gaze_y = 0.0
            else:
                self._target_gaze_x = self._rng.uniform(-4.5, 4.5)
                self._target_gaze_y = self._rng.uniform(-2.0, 2.0)

        self._gaze_x = _lerp(self._gaze_x, self._target_gaze_x, dt * 8.0)
        self._gaze_y = _lerp(self._gaze_y, self._target_gaze_y, dt * 8.0)

        # Natural blink timing (0 = open, 1 = fully closed)
        if not self._blinking and self.elapsed >= self._next_blink_at:
            self._blinking = True
            self._blink_progress = 0.0
            double_blink = self._rng.random() < 0.22
            self._next_blink_at = self.elapsed + (0.28 if double_blink else self._rng.uniform(2.2, 5.2))

        if self._blinking:
            self._blink_progress += dt / 0.18
            if self._blink_progress >= 1.0:
                self._blinking = False
                self._blink_progress = 0.0

    def _blink_factor(self) -> float:
        if not self._blinking:
            return 0.0
        # Triangle pulse: 0 -> 1 -> 0
        return 1.0 - abs(2.0 * self._blink_progress - 1.0)

    def render(self, fb: Framebuffer) -> None:
        """Draw the current expression frame onto the 64x32 framebuffer."""
        fb.clear()
        if self._boop_remaining > 0.0:
            self._draw_boop(fb)
            return

        if self.voice_state == "listening":
            self._draw_listening(fb)
            return
        if self.voice_state == "thinking":
            self._draw_thinking(fb)
            return

        speaking = self.voice_state == "speaking"
        dispatch = {
            "idle": self._draw_idle,
            "happy": self._draw_happy,
            "love": self._draw_love,
            "sleepy": self._draw_sleepy,
            "cool": self._draw_cool,
            "surprised": self._draw_surprised,
            "angry": self._draw_angry,
            "vibe": self._draw_vibe,
            "turbo": self._draw_turbo,
            "glitch": self._draw_glitch,
        }
        dispatch[self.mood](fb, speaking=speaking)

        if speaking:
            self._draw_speaking_overlay(fb)

    def _draw_listening(self, fb: Framebuffer) -> None:
        t = self.elapsed
        pulse = math.sin(t * 6.0) * 0.6
        eye_h = 13.0 + pulse
        fb.fill_rounded_rect(12.5, 12.0 - eye_h / 2.0, 13.0, eye_h, radius=3)
        fb.fill_rounded_rect(38.5, 12.0 - eye_h / 2.0, 13.0, eye_h, radius=3)

        # Live mic level bars across bottom
        level = max(0.08, min(1.0, self.mic_level))
        for i in range(9):
            dist = abs(i - 4) / 4.0
            wave_val = max(0.15, level * (1.15 - dist * 0.7) + 0.15 * abs(math.sin(t * 10.0 + i * 0.8)))
            bar_h = max(1, min(7, int(round(wave_val * 7.0))))
            bx = 14 + i * 4
            fb.fill_rect(bx, 29 - bar_h, 3, bar_h, PIXEL_ON if bar_h >= 3 else PIXEL_DIM)

    def _draw_thinking(self, fb: Framebuffer) -> None:
        t = self.elapsed
        # Curious eyes glancing up-right/left
        gx = math.sin(t * 2.2) * 2.5
        fb.fill_rounded_rect(12.5 + gx, 6.0, 13.0, 11.0, radius=3)
        fb.fill_rounded_rect(38.5 + gx, 6.0, 13.0, 11.0, radius=3)

        # Animated bouncing thinking dots
        active_dot = int(t * 4.5) % 3
        for idx in range(3):
            dx = 24 + idx * 7
            dy = 24 if idx != active_dot else 22
            val = PIXEL_ON if idx == active_dot else PIXEL_DIM
            fb.fill_rect(dx, dy, 3, 3, val)

    def _draw_speaking_overlay(self, fb: Framebuffer) -> None:
        t = self.elapsed
        phase = abs(math.sin(t * 14.0))
        mouth_y = 18 if self.mood in ("vibe", "turbo") else 22
        fb.fill_rect(23, mouth_y, 18, 6, 0)
        if phase < 0.25:
            fb.fill_rect(27, mouth_y + 2, 10, 2, PIXEL_ON)
        elif phase < 0.65:
            fb.fill_rounded_rect(26, mouth_y + 1, 12, 4, radius=1, value=PIXEL_ON)
        else:
            fb.fill_rounded_rect(26, mouth_y, 12, 5, radius=2, value=PIXEL_ON)
            fb.fill_rect(29, mouth_y + 2, 6, 2, PIXEL_DIM)

        # Tiny audio wave indicators on the sides
        if int(t * 8.0) % 2 == 0:
            fb.draw_line(5, 11, 5, 15, PIXEL_DIM)
            fb.draw_line(58, 11, 58, 15, PIXEL_DIM)
        else:
            fb.draw_line(3, 9, 3, 17, PIXEL_DIM)
            fb.draw_line(60, 9, 60, 17, PIXEL_DIM)

    def _draw_blush(self, fb: Framebuffer, gx: float, gy: float, y: float = 21.0) -> None:
        for side_x in (9 + gx * 0.5, 50 + gx * 0.5):
            ix = round(side_x)
            iy = round(y + gy * 0.4)
            fb.draw_line(ix, iy + 1, ix + 1, iy, PIXEL_DIM)
            fb.draw_line(ix + 3, iy + 1, ix + 4, iy, PIXEL_DIM)

    def _draw_cute_smile(self, fb: Framebuffer, cx: float, cy: float, width: int = 8) -> None:
        half = width // 2
        fb.draw_line(cx - half, cy - 1, cx - half + 1, cy, PIXEL_ON)
        fb.draw_line(cx - half + 1, cy, cx + half - 1, cy, PIXEL_ON)
        fb.draw_line(cx + half - 1, cy, cx + half, cy - 1, PIXEL_ON)

    def _draw_idle(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        bob = math.sin(t * 2.4) * 0.8
        gx = self._gaze_x
        gy = self._gaze_y + bob
        blink = self._blink_factor()

        eye_w = 13.0
        eye_h = max(2.0, 12.0 * (1.0 - blink * 0.92))
        top_y = 13.0 - eye_h / 2.0 + gy

        fb.fill_rounded_rect(19.0 - eye_w / 2.0 + gx, top_y, eye_w, eye_h, radius=3)
        fb.fill_rounded_rect(45.0 - eye_w / 2.0 + gx, top_y, eye_w, eye_h, radius=3)

        if not speaking:
            self._draw_cute_smile(fb, 32.0 + gx * 0.5, 24.5 + gy * 0.5, width=8)

    def _draw_happy(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        bounce = abs(math.sin(t * 5.2)) * -1.8
        gy = bounce

        # Thick ^ ^ arched eyes
        for cx in (19, 45):
            cy = 14.0 + gy
            fb.draw_line(cx - 6, cy + 2, cx, cy - 3, PIXEL_ON, thickness=2)
            fb.draw_line(cx, cy - 3, cx + 6, cy + 2, PIXEL_ON, thickness=2)

        if not speaking:
            fb.fill_rect(27, 21 + gy * 0.6, 11, 2, PIXEL_ON)
            fb.fill_rounded_rect(28, 22 + gy * 0.6, 9, 5, radius=2, value=PIXEL_ON)
            fb.fill_rect(30, 23 + gy * 0.6, 5, 2, PIXEL_DIM)

        self._draw_blush(fb, 0, gy, y=19.0)

        # Twinkling sparkles
        if int(t * 4) % 2 == 0:
            fb.draw_bitmap(3, 3, SPARKLE_BIG, value=PIXEL_DIM)
            fb.draw_bitmap(55, 6, SPARKLE_BIG, value=PIXEL_ON)
        else:
            fb.draw_bitmap(56, 3, SPARKLE_BIG, value=PIXEL_DIM)
            fb.draw_bitmap(4, 7, SPARKLE_BIG, value=PIXEL_ON)

    def _draw_boop(self, fb: Framebuffer) -> None:
        t = self.elapsed
        bounce = math.sin(t * 16.0) * 1.0
        # Super cute > < squint eyes
        fb.draw_line(13, 10 + bounce, 24, 14 + bounce, PIXEL_ON, thickness=2)
        fb.draw_line(13, 18 + bounce, 24, 14 + bounce, PIXEL_ON, thickness=2)

        fb.draw_line(51, 10 + bounce, 40, 14 + bounce, PIXEL_ON, thickness=2)
        fb.draw_line(51, 18 + bounce, 40, 14 + bounce, PIXEL_ON, thickness=2)

        # Cat 'ω' mouth
        fb.draw_line(26, 23 + bounce, 29, 25 + bounce, PIXEL_ON)
        fb.draw_line(29, 25 + bounce, 32, 23 + bounce, PIXEL_ON)
        fb.draw_line(32, 23 + bounce, 35, 25 + bounce, PIXEL_ON)
        fb.draw_line(35, 25 + bounce, 38, 23 + bounce, PIXEL_ON)

        self._draw_blush(fb, 0, bounce, y=20.0)
        fb.draw_bitmap(2, 3, HEART_MINI, value=PIXEL_ON)
        fb.draw_bitmap(56, 3, HEART_MINI, value=PIXEL_ON)

    def _draw_love(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        pulse = math.sin(t * 6.0)
        dy = -1 if pulse > 0.35 else 0

        fb.draw_bitmap(12, 8 + dy, HEART_BIG, value=PIXEL_ON)
        fb.draw_bitmap(39, 8 + dy, HEART_BIG, value=PIXEL_ON)
        self._draw_blush(fb, 0, dy * 0.5, y=21.0)
        if not speaking:
            self._draw_cute_smile(fb, 32, 25, width=8)

        # Rising floating mini hearts
        float_y1 = int(28 - (t * 8.0) % 28)
        float_y2 = int(28 - ((t * 8.0 + 14) % 28))
        fb.draw_bitmap(2, float_y1, HEART_MINI, value=PIXEL_DIM)
        fb.draw_bitmap(57, float_y2, HEART_MINI, value=PIXEL_DIM)

    def _draw_sleepy(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        nod = (math.sin(t * 1.4) + 1.0) * (0.6 if speaking else 1.5)
        blink = self._blink_factor()
        lid_drop = min(1.0, 0.55 + 0.35 * (0.5 + 0.5 * math.sin(t * 1.1)) + blink * 0.4)

        eye_h = max(2.0, 11.0 * (1.0 - lid_drop))
        for cx in (19, 45):
            fb.fill_rounded_rect(cx - 6, 15.0 + nod - eye_h / 2.0, 13, eye_h, radius=1)
            fb.fill_rect(cx - 7, 11.0 + nod, 15, 2, PIXEL_DIM)

        if not speaking:
            mouth_r = 1.6 + 0.5 * math.sin(t * 2.0)
            fb.draw_circle(32, 24.5 + nod * 0.6, mouth_r, value=PIXEL_ON, fill=False)

        # Drifting Zzz
        phase = (t * 0.9) % 2.0
        z1_y = round(10 - phase * 4)
        z2_y = round(6 - ((phase + 1.0) % 2.0) * 3)
        fb.draw_bitmap(55, max(0, z1_y), Z_SMALL, value=PIXEL_DIM)
        fb.draw_bitmap(57, max(0, z2_y), Z_LARGE, value=PIXEL_ON)

    def _draw_cool(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        bob = math.sin(t * 2.2) * 0.6
        y = 8.0 + bob

        # Sunglasses frame & lenses
        fb.fill_rect(8, y, 48, 2, PIXEL_ON)
        fb.fill_rounded_rect(10, y + 1, 19, 10, radius=2, value=PIXEL_ON)
        fb.fill_rounded_rect(35, y + 1, 19, 10, radius=2, value=PIXEL_ON)
        fb.fill_rect(29, y + 2, 6, 2, PIXEL_ON)

        fb.fill_rounded_rect(12, y + 3, 15, 6, radius=1, value=0)
        fb.fill_rounded_rect(37, y + 3, 15, 6, radius=1, value=0)

        shine_pos = (t * 28.0) % 72.0 - 12.0
        for sx_base in (12, 37):
            for row in range(6):
                px = round(sx_base + shine_pos - row)
                if sx_base <= px < sx_base + 15:
                    fb.set_pixel(px, round(y + 3 + row), PIXEL_ON)
                    if px + 1 < sx_base + 15:
                        fb.set_pixel(px + 1, round(y + 3 + row), PIXEL_DIM)

        if not speaking:
            fb.draw_line(27, 24 + bob * 0.5, 34, 24 + bob * 0.5, PIXEL_ON)
            fb.draw_line(34, 24 + bob * 0.5, 38, 22 + bob * 0.5, PIXEL_ON)

    def _draw_surprised(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        jitter_x = math.sin(t * 28.0) * 0.7
        jitter_y = math.cos(t * 23.0) * 0.7

        for cx in (19, 45):
            fb.draw_circle(cx + jitter_x, 13 + jitter_y, 7.2, value=PIXEL_ON, fill=True)
            fb.draw_circle(cx + jitter_x * 1.4, 13 + jitter_y * 1.4, 2.2, value=PIXEL_DIM, fill=True)

        if not speaking:
            fb.draw_circle(32, 25, 2.6, value=PIXEL_ON, fill=False)

        if int(t * 5) % 2 == 0:
            fb.fill_rect(57, 3, 2, 7, PIXEL_ON)
            fb.fill_rect(57, 12, 2, 2, PIXEL_ON)

    def _draw_angry(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        shake = math.sin(t * 32.0) * 0.7

        for i in range(11):
            y_top = 8 + i * 0.45
            fb.fill_rect(12 + i + shake, y_top, 2, 10 - i * 0.35, PIXEL_ON)
            fb.fill_rect(51 - i + shake, y_top, 2, 10 - i * 0.35, PIXEL_ON)

        if not speaking:
            fb.draw_line(24 + shake, 25, 28 + shake, 23, PIXEL_ON)
            fb.draw_line(28 + shake, 23, 32 + shake, 25, PIXEL_ON)
            fb.draw_line(32 + shake, 25, 36 + shake, 23, PIXEL_ON)
            fb.draw_line(36 + shake, 23, 40 + shake, 25, PIXEL_ON)

        if int(t * 4) % 2 == 0:
            fb.draw_bitmap(54, 2, ANGER_MARK, value=PIXEL_ON)
        else:
            fb.draw_bitmap(54, 2, ANGER_MARK, value=PIXEL_DIM)

    def _draw_vibe(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        beat = abs(math.sin(t * math.pi * 2.0))
        bob = -beat * 2.0

        for cx in (19, 45):
            fb.draw_line(cx - 6, 12 + bob, cx, 9 + bob, PIXEL_ON, thickness=2)
            fb.draw_line(cx, 9 + bob, cx + 6, 12 + bob, PIXEL_ON, thickness=2)

        if not speaking:
            self._draw_cute_smile(fb, 32, 18 + bob * 0.7, width=8)

        max_bar = 4 if speaking else 7
        for i in range(8):
            bar_h = 2 + int(round((math.sin(t * 7.5 + i * 0.9) * 0.5 + 0.5) * max_bar))
            bx = 10 + i * 6
            fb.fill_rect(bx, 31 - bar_h, 4, bar_h, PIXEL_ON if i % 2 == 0 else PIXEL_DIM)

        note1_y = int(10 - (t * 6.0) % 10)
        note2_y = int(10 - ((t * 6.0 + 5.0) % 10))
        fb.draw_bitmap(2, max(0, note1_y), NOTE_EIGHTH, value=PIXEL_ON)
        fb.draw_bitmap(56, max(0, note2_y), NOTE_BEAMED, value=PIXEL_DIM)

    def _draw_turbo(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        bob = math.sin(t * 14.0) * 0.4

        rows = ((3, 55.0), (7, 72.0)) if speaking else ((3, 55.0), (7, 72.0), (19, 64.0), (23, 80.0))
        for row, speed in rows:
            lx = int(64 - ((t * speed + row * 11) % 76))
            fb.fill_rect(lx, row, 10, 1, PIXEL_DIM)

        for cx in (19, 45):
            fb.fill_rounded_rect(cx - 7, 10 + bob, 14, 7, radius=2, value=PIXEL_ON)
            fb.fill_rect(cx - 7, 8 + bob, 14, 2, PIXEL_DIM)

        boost = 0.5 + 0.5 * math.sin(t * 2.5)
        fill_w = max(2, int(round(44 * boost)))
        bar_y = 25 if speaking else 24
        fb.fill_rect(9, bar_y, 46, 5, PIXEL_DIM)
        fb.fill_rect(10, bar_y + 1, 44, 3, 0)
        fb.fill_rect(10, bar_y + 1, fill_w, 3, PIXEL_ON)

    def _draw_glitch(self, fb: Framebuffer, *, speaking: bool = False) -> None:
        t = self.elapsed
        spin = t * 8.0

        if int(t * 2.5) % 4 == 3:
            for cx in (19, 45):
                fb.draw_line(cx - 5, 8, cx + 5, 18, PIXEL_ON, thickness=2)
                fb.draw_line(cx + 5, 8, cx - 5, 18, PIXEL_ON, thickness=2)
        else:
            for idx, cx in enumerate((19, 45)):
                fb.draw_circle(cx, 13, 6.2, value=PIXEL_ON, fill=False)
                fb.draw_circle(cx, 13, 3.0, value=PIXEL_DIM, fill=False)
                px = cx + math.cos(spin + idx * math.pi) * 2.5
                py = 13 + math.sin(spin + idx * math.pi) * 2.5
                fb.fill_rect(px - 1, py - 1, 2, 2, PIXEL_ON)

        if not speaking:
            for i in range(-6, 7):
                my = 25 + round(math.sin(t * 14.0 + i * 0.9) * 1.4)
                fb.set_pixel(32 + i, my, PIXEL_ON)

        tick = int(t * 18.0)
        if tick % 5 == 0:
            tear_y = (tick * 7) % (21 if speaking else 28)
            shift = 3 if (tick % 2 == 0) else -3
            row = fb.pixels[tear_y][:]
            for x in range(fb.width):
                fb.pixels[tear_y][x] = row[(x - shift) % fb.width]
            fb.fill_rect((tick * 13) % 50, (tear_y + 2) % 31, 9, 1, PIXEL_DIM)
