"""OLED phosphor color themes and curses/ANSI color helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    ansi_fg: str
    ansi_dim: str
    ansi_bezel: str
    curses_256_fg: int
    curses_256_dim: int
    curses_basic_fg: int


# Basic curses color indices match standard curses.COLOR_* constants:
# BLACK=0, RED=1, GREEN=2, YELLOW=3, BLUE=4, MAGENTA=5, CYAN=6, WHITE=7
THEMES: dict[str, Theme] = {
    "cyan": Theme(
        name="cyan",
        label="Cyber Cyan",
        ansi_fg="\033[38;5;51m",
        ansi_dim="\033[38;5;30m",
        ansi_bezel="\033[38;5;242m",
        curses_256_fg=51,
        curses_256_dim=30,
        curses_basic_fg=6,
    ),
    "white": Theme(
        name="white",
        label="Classic OLED",
        ansi_fg="\033[38;5;255m",
        ansi_dim="\033[38;5;244m",
        ansi_bezel="\033[38;5;240m",
        curses_256_fg=255,
        curses_256_dim=244,
        curses_basic_fg=7,
    ),
    "amber": Theme(
        name="amber",
        label="JDM Amber",
        ansi_fg="\033[38;5;214m",
        ansi_dim="\033[38;5;130m",
        ansi_bezel="\033[38;5;242m",
        curses_256_fg=214,
        curses_256_dim=130,
        curses_basic_fg=3,
    ),
    "green": Theme(
        name="green",
        label="Phosphor Green",
        ansi_fg="\033[38;5;48m",
        ansi_dim="\033[38;5;29m",
        ansi_bezel="\033[38;5;242m",
        curses_256_fg=48,
        curses_256_dim=29,
        curses_basic_fg=2,
    ),
    "pink": Theme(
        name="pink",
        label="Sakura Pink",
        ansi_fg="\033[38;5;212m",
        ansi_dim="\033[38;5;132m",
        ansi_bezel="\033[38;5;242m",
        curses_256_fg=212,
        curses_256_dim=132,
        curses_basic_fg=5,
    ),
    "red": Theme(
        name="red",
        label="Type-R Red",
        ansi_fg="\033[38;5;196m",
        ansi_dim="\033[38;5;88m",
        ansi_bezel="\033[38;5;242m",
        curses_256_fg=196,
        curses_256_dim=88,
        curses_basic_fg=1,
    ),
}

THEME_ORDER: tuple[str, ...] = tuple(THEMES.keys())


def get_theme(name: str) -> Theme:
    key = name.strip().lower()
    if key not in THEMES:
        valid = ", ".join(THEME_ORDER)
        raise ValueError(f"Unknown theme '{name}'. Choose from: {valid}")
    return THEMES[key]


def next_theme(current: str) -> str:
    idx = THEME_ORDER.index(current) if current in THEME_ORDER else 0
    return THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
