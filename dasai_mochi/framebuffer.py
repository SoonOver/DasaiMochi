"""64x32 virtual OLED framebuffer and terminal cell renderers."""

from __future__ import annotations

from dataclasses import dataclass, field


# Pixel intensity levels:
# 0 = off (visor dark)
# 1 = dim (glow / anti-alias / background detail)
# 2 = bright (primary OLED pixel)
PIXEL_OFF = 0
PIXEL_DIM = 1
PIXEL_ON = 2


@dataclass
class Framebuffer:
    """2D monochrome-with-dim-level pixel buffer simulating an OLED screen."""

    width: int = 64
    height: int = 32
    pixels: list[list[int]] = field(init=False)

    def __post_init__(self) -> None:
        self.pixels = [[PIXEL_OFF] * self.width for _ in range(self.height)]

    def clear(self, value: int = PIXEL_OFF) -> None:
        for row in self.pixels:
            for x in range(self.width):
                row[x] = value

    def set_pixel(self, x: int, y: int, value: int = PIXEL_ON) -> None:
        ix = round(x)
        iy = round(y)
        if 0 <= ix < self.width and 0 <= iy < self.height:
            self.pixels[iy][ix] = value

    def get_pixel(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.pixels[y][x]
        return PIXEL_OFF

    def fill_rect(self, x: float, y: float, w: float, h: float, value: int = PIXEL_ON) -> None:
        x0 = max(0, round(x))
        y0 = max(0, round(y))
        x1 = min(self.width, round(x + w))
        y1 = min(self.height, round(y + h))
        for py in range(y0, y1):
            row = self.pixels[py]
            for px in range(x0, x1):
                row[px] = value

    def fill_rounded_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        radius: float = 2,
        value: int = PIXEL_ON,
    ) -> None:
        """Fill a rectangle with clipped/rounded corners."""
        x0 = round(x)
        y0 = round(y)
        rw = max(1, round(w))
        rh = max(1, round(h))
        r = max(0, min(round(radius), rw // 2, rh // 2))

        for dy in range(rh):
            py = y0 + dy
            if not (0 <= py < self.height):
                continue
            row = self.pixels[py]
            for dx in range(rw):
                px = x0 + dx
                if not (0 <= px < self.width):
                    continue
                if r > 0:
                    in_corner = False
                    cx = cy = 0
                    if dx < r and dy < r:
                        cx, cy, in_corner = r - 1 - dx, r - 1 - dy, True
                    elif dx >= rw - r and dy < r:
                        cx, cy, in_corner = dx - (rw - r), r - 1 - dy, True
                    elif dx < r and dy >= rh - r:
                        cx, cy, in_corner = r - 1 - dx, dy - (rh - r), True
                    elif dx >= rw - r and dy >= rh - r:
                        cx, cy, in_corner = dx - (rw - r), dy - (rh - r), True
                    if in_corner and ((cx + 0.5) ** 2 + (cy + 0.5) ** 2) > (r * r):
                        continue
                row[px] = value

    def draw_circle(self, cx: float, cy: float, radius: float, value: int = PIXEL_ON, fill: bool = True) -> None:
        r = max(0.0, radius)
        r_int = int(r) + 1
        icx = round(cx)
        icy = round(cy)
        for dy in range(-r_int, r_int + 1):
            for dx in range(-r_int, r_int + 1):
                dist2 = dx * dx + dy * dy
                if fill:
                    if dist2 <= r * r + 0.35:
                        self.set_pixel(icx + dx, icy + dy, value)
                else:
                    if (r - 0.8) ** 2 <= dist2 <= (r + 0.6) ** 2:
                        self.set_pixel(icx + dx, icy + dy, value)

    def draw_line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        value: int = PIXEL_ON,
        thickness: int = 1,
    ) -> None:
        ix0, iy0 = round(x0), round(y0)
        ix1, iy1 = round(x1), round(y1)
        dx = abs(ix1 - ix0)
        dy = -abs(iy1 - iy0)
        sx = 1 if ix0 < ix1 else -1
        sy = 1 if iy0 < iy1 else -1
        err = dx + dy

        half = max(0, thickness // 2)
        while True:
            if thickness <= 1:
                self.set_pixel(ix0, iy0, value)
            else:
                self.fill_rect(ix0 - half, iy0 - half, thickness, thickness, value)
            if ix0 == ix1 and iy0 == iy1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                ix0 += sx
            if e2 <= dx:
                err += dx
                iy0 += sy

    def draw_bitmap(
        self,
        x: int,
        y: int,
        rows: list[str] | tuple[str, ...],
        value: int = PIXEL_ON,
        dim_char: str = ".",
    ) -> None:
        """Draw ASCII bitmap where '#'/'*'/'@' = ON, dim_char = DIM, space = transparent."""
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch in ("#", "*", "@", "1"):
                    self.set_pixel(x + dx, y + dy, value)
                elif ch == dim_char and dim_char != " ":
                    self.set_pixel(x + dx, y + dy, PIXEL_DIM)

    def downsample_half(self) -> Framebuffer:
        """Create a half-resolution copy (e.g. 64x32 -> 32x16) for compact terminals."""
        out = Framebuffer(max(2, self.width // 2), max(2, self.height // 2))
        for py in range(out.height):
            sy = py * 2
            for px in range(out.width):
                sx = px * 2
                best = max(
                    self.get_pixel(sx, sy),
                    self.get_pixel(sx + 1, sy),
                    self.get_pixel(sx, sy + 1),
                    self.get_pixel(sx + 1, sy + 1),
                )
                out.pixels[py][px] = best
        return out

    def to_half_blocks(self) -> list[list[tuple[str, bool]]]:
        """Render pairs of rows into Unicode half-block cells.

        Returns rows of `(char, is_dim)` where `is_dim` hints whether the lit
        pixels in that cell are dim-only (for subtle shading).
        """
        rendered: list[list[tuple[str, bool]]] = []
        for y in range(0, self.height, 2):
            row_cells: list[tuple[str, bool]] = []
            top_row = self.pixels[y]
            bot_row = self.pixels[y + 1] if y + 1 < self.height else [PIXEL_OFF] * self.width
            for x in range(self.width):
                top = top_row[x]
                bot = bot_row[x]
                top_on = top > PIXEL_OFF
                bot_on = bot > PIXEL_OFF
                if top_on and bot_on:
                    ch = "█"
                    dim = top == PIXEL_DIM and bot == PIXEL_DIM
                elif top_on:
                    ch = "▀"
                    dim = top == PIXEL_DIM
                elif bot_on:
                    ch = "▄"
                    dim = bot == PIXEL_DIM
                else:
                    ch = " "
                    dim = False
                row_cells.append((ch, dim))
            rendered.append(row_cells)
        return rendered

    def to_ascii_rows(self) -> list[list[tuple[str, bool]]]:
        """Render pairs of rows into pure ASCII density characters."""
        rendered: list[list[tuple[str, bool]]] = []
        for y in range(0, self.height, 2):
            row_cells: list[tuple[str, bool]] = []
            top_row = self.pixels[y]
            bot_row = self.pixels[y + 1] if y + 1 < self.height else [PIXEL_OFF] * self.width
            for x in range(self.width):
                top = top_row[x]
                bot = bot_row[x]
                score = top + bot
                if score == 0:
                    row_cells.append((" ", False))
                elif score == 1:
                    row_cells.append((".", True))
                elif score == 2:
                    ch = ":" if (top == PIXEL_DIM and bot == PIXEL_DIM) else "+"
                    row_cells.append((ch, top == PIXEL_DIM and bot == PIXEL_DIM))
                elif score == 3:
                    row_cells.append(("*", False))
                else:
                    row_cells.append(("#", False))
            rendered.append(row_cells)
        return rendered


@dataclass(frozen=True)
class BezelSpec:
    """Aligned helmet bezel geometry around an OLED screen of width `screen_w`."""

    top_lines: tuple[str, ...]
    left_pad: str
    right_pad: str
    bottom_lines: tuple[str, ...]
    total_width: int
    top_height: int
    bottom_height: int


def build_bezel(screen_w: int, compact: bool = False) -> BezelSpec:
    """Build exact-width helmet bezel lines matching `screen_w`."""
    if compact:
        bar = "─" * screen_w
        return BezelSpec(
            top_lines=(f"╭─{bar}─╮",),
            left_pad="│ ",
            right_pad=" │",
            bottom_lines=(f"╰─{bar}─╯",),
            total_width=screen_w + 4,
            top_height=1,
            bottom_height=1,
        )

    inner_w = screen_w + 2
    gap_w = max(0, screen_w - 10)
    ears = f"   ▄████▄{' ' * gap_w}▄████▄   "
    outer_top = f" ╭─{'─' * inner_w}─╮ "
    visor_top = f"╭┤ ┌{'─' * screen_w}┐ ├╮"
    visor_bot = f"╰┤ └{'─' * screen_w}┘ ├╯"
    brand = " DASAI MOCHI "
    left_fill = max(0, (inner_w - len(brand)) // 2)
    right_fill = max(0, inner_w - len(brand) - left_fill)
    outer_bot = f" ╰─{'─' * left_fill}{brand}{'─' * right_fill}─╯ "

    return BezelSpec(
        top_lines=(ears, outer_top, visor_top),
        left_pad="││ │",
        right_pad="│ ││",
        bottom_lines=(visor_bot, outer_bot),
        total_width=screen_w + 8,
        top_height=3,
        bottom_height=2,
    )

