"""Shared look for all three formats."""

FONT = "Calibri"
PRIMARY = "1F3A5F"
ACCENT = "2E86AB"
LIGHT = "EAF1F8"
TEXT = "222222"
WHITE = "FFFFFF"

FORMATS = {
    "number": "#,##0.00",
    "currency": "#,##0.00",
    "percent": "0.0%",
}

# Layout constants shared by the PPTX renderer and validator (inches, 16:9 slide).
SLIDE_W, SLIDE_H = 13.333, 7.5
MARGIN = 0.6
TITLE_H = 1.0
