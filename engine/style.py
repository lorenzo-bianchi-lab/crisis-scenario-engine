"""Visual tokens shared by the app and by the exported documents: every colour and typeface
is defined here and nowhere else (dev/SPEC_UI.md).

Pure Python: no Plotly, no Streamlit, no file access. `app/theme.py` builds the Plotly
template, the CSS and the Streamlit theme from these tokens; `engine/export.py` and
`engine/docfigures.py` style the Word documents and their charts with them, so documents
written from the command line and from the app look the same.
"""

from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------------------- typefaces

FONT_TEXT = '"Source Sans", "Source Sans 3", "Helvetica Neue", Arial, sans-serif'
# Figures: fixed-width numerals, so columns of numbers line up.
FONT_FIGURES = '"Source Code Pro", "SFMono-Regular", Menlo, Consolas, monospace'
# Streamlit's own names for the two families above (theme.font and theme.codeFont).
STREAMLIT_FONT, STREAMLIT_CODE_FONT = "sans-serif", "monospace"

# ----------------------------------------------------------------------------- stages

# The fixed scale of the bank's stage, in order of gravity.
STAGES = ("normal", "recovery", "early_intervention", "likely_to_fail", "foltf", "default")
STAGE_LABELS = {
    "normal": "Normal",
    "recovery": "Recovery",
    "early_intervention": "Early intervention",
    "likely_to_fail": "Likely to fail",
    "foltf": "FoLtF / resolution",
    "default": "Payments default",
}
# A shape for each stage: colour is never the only signal.
STAGE_ICONS = {
    "normal": "○",  # white circle
    "recovery": "◔",  # circle, one quarter filled
    "early_intervention": "◑",  # circle, half filled
    "likely_to_fail": "◕",  # circle, three quarters filled
    "foltf": "◆",  # black diamond
    "default": "■",  # black square
}
# The engine's words (status of a day, kind of an event, outcome of a run) -> stage.
_STAGE_OF = {
    "failed": "default", "failure": "default", "payments_default": "default",
    "resolution": "foltf", "liquidation": "foltf", "bail_in": "foltf",
    "sale_of_business": "foltf",
    # Outcomes of a bank still open at the end of the run.
    "stabilised": "normal", "under_stress": "recovery", "emergency_funding": "early_intervention",
}

# Funding sources of the daily waterfall, in the order they are used.
SOURCES = ("cash", "hqla", "central_bank", "ela", "fire_sale", "unmet")
SOURCE_LABELS = {
    "cash": "Cash",
    "hqla": "Liquid securities",
    "central_bank": "Central bank",
    "ela": "ELA",
    "fire_sale": "Asset sales",
    "unmet": "Unmet",
}

# Purpose of an inject (EBA Handbook: advance, challenge or control the exercise), with a
# marker shape for each, as Plotly names it.
PURPOSES = ("advance", "challenge", "control")
PURPOSE_LABELS = {"advance": "Advance", "challenge": "Challenge", "control": "Control"}
PURPOSE_SYMBOLS = {"advance": "circle", "challenge": "diamond", "control": "square"}


@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    ink: str  # the dark neutral: text, axes
    paper: str  # the light background of the page
    surface: str  # cards, tables, the plot area
    line: str  # borders and grid lines
    muted: str  # secondary text
    accent: str  # the one accent: main data line, primary buttons, current step
    on_accent: str  # text on the accent
    reference: str  # the second series of a comparison
    stages: dict[str, str]
    sources: dict[str, str]
    purposes: dict[str, str]


PALETTES = {
    "A": Palette(
        key="A", name="Slate and teal",
        ink="#17202A", paper="#FFFFFF", surface="#FFFFFF", line="#DAD6CC", muted="#5B6670",
        accent="#0B6E69", on_accent="#FFFFFF", reference="#7A7F87",
        stages={
            "normal": "#8A9BA8", "recovery": "#E9B824", "early_intervention": "#E07B39",
            "likely_to_fail": "#C8323C", "foltf": "#6B3FA0", "default": "#2B1B2E",
        },
        sources={
            "cash": "#1D4E5F", "hqla": "#3E8E9D", "central_bank": "#9CCBD3", "ela": "#7A5BB5",
            "fire_sale": "#D9A441", "unmet": "#2B1B2E",
        },
        purposes={"advance": "#1F5FA8", "challenge": "#C2410C", "control": "#3D3A45"},
    ),
}
DEFAULT_PALETTE = "A"


def palette(key: str | None = None) -> Palette:
    """The palette with this key; without one, the app's palette."""
    key = key or DEFAULT_PALETTE
    if key not in PALETTES:
        raise ValueError(f"unknown palette '{key}': expected one of {sorted(PALETTES)}")
    return PALETTES[key]


# ----------------------------------------------------------------------------- colour maths


def _linear(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    channels = (int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)


def luminance(color: str) -> float:
    r, g, b = _linear(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio of two colours (1 to 21)."""
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# Colour-vision deficiency at full severity (Machado, Oliveira and Fernandes, 2009), applied
# to linear RGB.
CVD_MATRICES = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}


def _oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def distance(first: str, second: str, vision: str | None = None) -> float:
    """Distance of two colours in OKLab x 100, as seen with a colour-vision deficiency if
    `vision` names one. Below about 8, two colours are hard to tell apart."""
    pair = []
    for color in (first, second):
        rgb = _linear(color)
        if vision is not None:
            matrix = CVD_MATRICES[vision]
            rgb = tuple(sum(matrix[i][j] * rgb[j] for j in range(3)) for i in range(3))
        pair.append(_oklab(rgb))
    return 100 * sum((a - b) ** 2 for a, b in zip(*pair)) ** 0.5


def text_on(color: str, pal: Palette | None = None) -> str:
    """The text colour to put on `color`: the ink or white, whichever contrasts more."""
    pal = pal or palette()
    return pal.ink if contrast(pal.ink, color) >= contrast("#FFFFFF", color) else "#FFFFFF"


def rgba(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def mix(color: str, other: str, share: float) -> str:
    """`color` mixed into `other`: share 1 gives `color`, 0 gives `other`."""
    a, b = color.lstrip("#"), other.lstrip("#")
    return "#" + "".join(
        f"{round(int(a[i:i + 2], 16) * share + int(b[i:i + 2], 16) * (1 - share)):02X}"
        for i in (0, 2, 4)
    )


# ----------------------------------------------------------------------------- stages in use


def stage_of(word: str) -> str:
    """The stage of the scale for one of the engine's words: a day's status, an event's
    kind or a run's outcome."""
    stage = _STAGE_OF.get(word, word)
    if stage not in STAGES:
        raise ValueError(f"'{word}' has no place on the stage scale")
    return stage


def stage_color(word: str, pal: Palette | None = None) -> str:
    return (pal or palette()).stages[stage_of(word)]


def stage_label(word: str) -> str:
    return STAGE_LABELS[stage_of(word)]


# ----------------------------------------------------------------------------- documents

# Typefaces of the Word documents: installed with Word, so nothing is substituted.
DOC_FONT_TEXT = "Calibri"
DOC_FONT_FIGURES = "Consolas"
# Typefaces tried in turn by the charts drawn for the documents, on the computer that
# writes them; the last one ships with matplotlib.
CHART_FONTS = ("Calibri", "Carlito", "Helvetica Neue", "Arial", "DejaVu Sans")
