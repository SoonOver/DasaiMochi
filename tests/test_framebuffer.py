"""Tests for the virtual OLED framebuffer and bezel geometry."""

from dasai_mochi.framebuffer import PIXEL_DIM, PIXEL_OFF, PIXEL_ON, Framebuffer, build_bezel


def test_framebuffer_primitives_and_bounds():
    fb = Framebuffer(16, 8)
    fb.set_pixel(-5, -5, PIXEL_ON)
    fb.set_pixel(100, 100, PIXEL_ON)
    assert fb.get_pixel(0, 0) == PIXEL_OFF

    fb.fill_rect(2, 2, 4, 2, PIXEL_ON)
    assert fb.get_pixel(2, 2) == PIXEL_ON
    assert fb.get_pixel(5, 3) == PIXEL_ON
    assert fb.get_pixel(6, 3) == PIXEL_OFF

    fb.clear()
    assert fb.get_pixel(2, 2) == PIXEL_OFF


def test_half_blocks_and_ascii_renderers():
    fb = Framebuffer(4, 4)
    fb.set_pixel(0, 0, PIXEL_ON)
    fb.set_pixel(1, 1, PIXEL_ON)
    fb.set_pixel(2, 0, PIXEL_ON)
    fb.set_pixel(2, 1, PIXEL_ON)
    fb.set_pixel(3, 0, PIXEL_DIM)

    blocks = fb.to_half_blocks()
    assert len(blocks) == 2
    assert [cell[0] for cell in blocks[0]] == ["▀", "▄", "█", "▀"]
    assert blocks[0][3][1] is True
    assert blocks[0][0][1] is False

    ascii_rows = fb.to_ascii_rows()
    assert [cell[0] for cell in ascii_rows[0]] == ["+", "+", "#", "."]


def test_downsample_half_preserves_lit_pixels():
    fb = Framebuffer(64, 32)
    fb.fill_rounded_rect(10, 8, 12, 10, radius=2, value=PIXEL_ON)
    small = fb.downsample_half()
    assert small.width == 32
    assert small.height == 16
    assert any(px == PIXEL_ON for row in small.pixels for px in row)


def test_bezel_widths_align_with_screen():
    for compact in (False, True):
        screen_w = 32 if compact else 64
        bezel = build_bezel(screen_w, compact=compact)
        for line in (*bezel.top_lines, *bezel.bottom_lines):
            assert len(line) == bezel.total_width
        assert len(bezel.left_pad) + screen_w + len(bezel.right_pad) == bezel.total_width
