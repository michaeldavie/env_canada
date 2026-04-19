import os

from PIL import Image, ImageDraw, ImageFont

_font_path = os.path.join(os.path.dirname(__file__), "DejaVuSans.ttf")

__all__ = ["generate_legend", "load_font"]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path, size)


# ── Color tables ──────────────────────────────────────────────────────────────
# Colors sampled from the Environment Canada Weather Information map legend
# https://weather.gc.ca

_PRECIP_TYPE_GROUPS: dict[str, list[tuple[str, list[str]]]] = {
    "english": [
        ("Snow", ["#97deea", "#61a3eb", "#0000f5"]),
        ("Rain/snow mix", ["#dcc0e4", "#b972ce", "#9031aa"]),
        ("Rain", ["#b2fca5", "#5cc93b", "#377e22"]),
        ("Hail/rain", ["#f6c444"]),
        ("Freezing rain", ["#f5ceca", "#cf3724"]),
    ],
    "french": [
        ("Neige", ["#97deea", "#61a3eb", "#0000f5"]),
        ("Mixte pluie /\nneige", ["#dcc0e4", "#b972ce", "#9031aa"]),
        ("Pluie", ["#b2fca5", "#5cc93b", "#377e22"]),
        ("Grêle / pluie", ["#f6c444"]),
        ("Pluie\nverglaçante", ["#f5ceca", "#cf3724"]),
    ],
}

# Each entry: (threshold_label, hex_color)
# Colors match the 14-band RADAR_1KM_RRAI / RADAR_1KM_RSNO WMS styles
_RAIN_SCALE: list[tuple[str, str]] = [
    ("0.1", "#9eb2c6"),
    ("1", "#3cb7ec"),
    ("2", "#00e136"),
    ("4", "#00b500"),
    ("8", "#007900"),
    ("12", "#cde029"),
    ("16", "#fecb00"),
    ("24", "#fe8e00"),
    ("32", "#fe3f00"),
    ("50", "#fe0157"),
    ("64", "#b922ba"),
    ("100", "#6f09a1"),
    ("150", "#660098"),
    ("200+", "#9b78ad"),
]

_SNOW_SCALE: list[tuple[str, str]] = [
    ("0.1", "#9eb2c6"),
    ("0.2", "#0098fe"),
    ("0.3", "#00fe66"),
    ("0.5", "#00cb00"),
    ("0.75", "#009800"),
    ("1.0", "#006600"),
    ("1.5", "#fefe00"),
    ("2.0", "#fecb00"),
    ("3.0", "#fe9800"),
    ("4.0", "#fe6600"),
    ("5.0", "#fe0000"),
    ("7.5", "#fe0298"),
    ("10", "#9833cb"),
    ("20+", "#660098"),
]

_SCALE_UNITS: dict[str, dict[str, str]] = {
    "rain": {"english": "mm/h", "french": "mm/h"},
    "snow": {"english": "cm/h", "french": "cm/h"},
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_PAD = 8
_SWATCH_H = 28
_GROUP_GAP = 6  # extra horizontal gap between precip_type groups


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _text_size(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


# ── Public API ────────────────────────────────────────────────────────────────


def generate_legend(
    layer: str, language: str = "english", width: int = 800
) -> Image.Image:
    """Return a PIL Image containing a horizontal legend for *layer*."""
    if layer == "precip_type":
        return _precip_type_legend(language, width)
    if layer in ("rain", "snow"):
        return _intensity_legend(layer, language, width)
    raise ValueError(f"No legend defined for layer {layer!r}")


# ── Precip-type legend ────────────────────────────────────────────────────────


def _precip_type_legend(language: str, width: int) -> Image.Image:
    groups = _PRECIP_TYPE_GROUPS[language]
    n = len(groups)
    font = load_font(13)

    # Measure tallest label (some labels are two lines)
    _, ch = _text_size(font, "A")
    line_h = ch + 2
    max_label_h = max(len(label.split("\n")) * line_h for label, _ in groups)

    total_gap = _GROUP_GAP * (n - 1)
    usable_w = width - _PAD * 2 - total_gap
    group_w = usable_w // n
    total_h = _PAD + _SWATCH_H + 6 + max_label_h + _PAD

    img = Image.new("RGB", (width, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for gi, (label, swatches) in enumerate(groups):
        gx = _PAD + gi * (group_w + _GROUP_GAP)
        sw_w = group_w // len(swatches)

        for si, color in enumerate(swatches):
            sx = gx + si * sw_w
            ex = gx + (si + 1) * sw_w if si < len(swatches) - 1 else gx + group_w
            draw.rectangle([sx, _PAD, ex, _PAD + _SWATCH_H], fill=_hex_to_rgb(color))

        lines = label.split("\n")
        ty = _PAD + _SWATCH_H + 6
        for line in lines:
            lw, _ = _text_size(font, line)
            tx = gx + (group_w - lw) // 2
            draw.text((tx, ty), line, fill=(30, 30, 30), font=font)
            ty += line_h

    return img


# ── Rain / snow intensity legend ──────────────────────────────────────────────


def _intensity_legend(layer: str, language: str, width: int) -> Image.Image:
    scale = _RAIN_SCALE if layer == "rain" else _SNOW_SCALE
    units = _SCALE_UNITS[layer][language]
    font = load_font(11)
    font_units = load_font(11)

    _, ch = _text_size(font, "0")
    line_h = ch + 2
    n = len(scale)
    band_w = (width - _PAD * 2) // n

    total_h = _PAD + _SWATCH_H + 4 + line_h + 4 + line_h + _PAD
    img = Image.new("RGB", (width, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for i, (val, color) in enumerate(scale):
        bx = _PAD + i * band_w
        ex = _PAD + (i + 1) * band_w if i < n - 1 else width - _PAD
        draw.rectangle([bx, _PAD, ex, _PAD + _SWATCH_H], fill=_hex_to_rgb(color))
        lw, _ = _text_size(font, val)
        tx = bx + (band_w - lw) // 2
        draw.text((tx, _PAD + _SWATCH_H + 4), val, fill=(30, 30, 30), font=font)

    # Units on second line, centred
    uw, _ = _text_size(font_units, units)
    draw.text(
        ((width - uw) // 2, _PAD + _SWATCH_H + 4 + line_h + 2),
        units,
        fill=(80, 80, 80),
        font=font_units,
    )

    return img
