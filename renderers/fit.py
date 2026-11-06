"""estimates text fit and helps avoid slide overflow
"""
from __future__ import annotations

import math
from typing import Sequence

AVG_CHAR_EM = 0.5
LINE_SPACING = 1.2
PARA_GAP_PT = 6.0


def estimate_lines(text: str, font_pt: float, width_in: float) -> int:
    chars_per_line = max(1, int(width_in * 72 / (font_pt * AVG_CHAR_EM)))
    return max(1, math.ceil(len(text) / chars_per_line))


def text_height_in(paragraphs: Sequence[str], font_pt: float, width_in: float) -> float:
    lines = sum(estimate_lines(p, font_pt, width_in) for p in paragraphs)
    pts = lines * font_pt * LINE_SPACING + max(0, len(paragraphs) - 1) * PARA_GAP_PT
    return pts / 72


def fit_font(paragraphs: Sequence[str], width_in: float, height_in: float, max_pt: float, min_pt: float) -> tuple[float, bool]:
    """Largest font size (step 1 pt) at which the text fits. Returns (size, fits)."""
    size = max_pt
    while size >= min_pt:
        if text_height_in(paragraphs, size, width_in) <= height_in:
            return size, True
        size -= 1
    return min_pt, False
