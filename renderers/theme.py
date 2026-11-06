"""Shared look for all three formats."""

FONT = "Aptos"
PRIMARY = "263B35"
ACCENT = "B17A55"
LIGHT = "F5F1E9"
TEXT = "292C29"
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
