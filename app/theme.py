"""Visual system of the app (dev/SPEC_UI.md): the Plotly template, the CSS variables and
the Streamlit theme, built from the tokens of engine/style.py.

Colours and typefaces are defined in engine/style.py only, which the exported documents
read too; they are re-exported here for the app. No Streamlit import, so charts and tests
can use this module alone. The typefaces are the ones Streamlit ships and serves itself, so
nothing is fetched from a CDN at runtime.

`python -m app.theme` prints the [theme] block of .streamlit/config.toml.
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go

from engine.style import (  # noqa: F401  (the tokens, re-exported for the app)
    CVD_MATRICES, DEFAULT_PALETTE, FONT_FIGURES, FONT_TEXT, PALETTES, PURPOSE_LABELS,
    PURPOSE_SYMBOLS, PURPOSES, SOURCE_LABELS, SOURCES, STAGE_ICONS, STAGE_LABELS, STAGES,
    STREAMLIT_CODE_FONT, STREAMLIT_FONT, Palette, contrast, distance, luminance, mix, palette,
    rgba, stage_color, stage_label, stage_of, text_on,
)

# ----------------------------------------------------------------------------- stages in use


def stage_badge(word: str, text: str | None = None, pal: Palette | None = None) -> str:
    """HTML of a badge: the stage's shape and label on the stage's colour."""
    stage = stage_of(word)
    color = stage_color(stage, pal)
    return (
        f'<span class="cse-badge" style="background:{color};color:{text_on(color, pal)}">'
        f'{STAGE_ICONS[stage]}&nbsp;{text or STAGE_LABELS[stage]}</span>'
    )


# ----------------------------------------------------------------------------- Plotly

TEMPLATE_META = "crisis-scenario-engine"


def plotly_template(pal: Palette | None = None) -> go.layout.Template:
    """The one Plotly template of the app."""
    pal = pal or palette()
    axis = dict(
        gridcolor=mix(pal.line, pal.surface, 0.6), linecolor=pal.line, zerolinecolor=pal.line,
        tickfont=dict(family=FONT_FIGURES, size=13, color=pal.muted),
        title=dict(font=dict(family=FONT_TEXT, size=14, color=pal.muted)),
        ticks="outside", tickcolor=pal.line, automargin=True,
    )
    return go.layout.Template(layout=dict(
        meta=TEMPLATE_META,
        font=dict(family=FONT_TEXT, size=15, color=pal.ink),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=pal.surface,
        colorway=[pal.accent, pal.reference, *pal.sources.values()],
        xaxis=axis, yaxis=axis,
        legend=dict(font=dict(family=FONT_TEXT, size=14, color=pal.ink),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(font=dict(family=FONT_TEXT, size=14), bgcolor=pal.surface,
                        bordercolor=pal.line),
        annotationdefaults=dict(font=dict(family=FONT_TEXT, size=13, color=pal.ink)),
        margin=dict(l=10, r=10, t=40, b=10),
    ))


def apply(fig: go.Figure, pal: Palette | None = None) -> go.Figure:
    """Give a figure the app's template. Every chart builder ends with this."""
    fig.update_layout(template=plotly_template(pal))
    return fig


def uses_template(fig: go.Figure) -> bool:
    return fig.layout.template.layout.meta == TEMPLATE_META


# ----------------------------------------------------------------------------- Streamlit

CSS_FILE = Path(__file__).resolve().parent / "theme.css"


def css_variables(pal: Palette | None = None) -> str:
    pal = pal or palette()
    values = {
        "ink": pal.ink, "paper": pal.paper, "surface": pal.surface, "line": pal.line,
        "muted": pal.muted, "accent": pal.accent, "on-accent": pal.on_accent,
        "accent-soft": mix(pal.accent, pal.paper, 0.12),
        "font-text": FONT_TEXT, "font-figures": FONT_FIGURES,
    }
    values.update({f"stage-{stage.replace('_', '-')}": color for stage, color in pal.stages.items()})
    return ":root {" + " ".join(f"--cse-{name}: {value};" for name, value in values.items()) + "}"


def css(pal: Palette | None = None) -> str:
    """The palette's variables followed by the rules of app/theme.css."""
    return css_variables(pal) + "\n" + CSS_FILE.read_text(encoding="utf-8")


def streamlit_theme(pal: Palette | None = None) -> dict[str, str]:
    """The [theme] options of Streamlit for a palette."""
    pal = pal or palette()
    return {
        "base": "light",
        "primaryColor": pal.accent,
        "backgroundColor": pal.paper,
        "secondaryBackgroundColor": pal.surface,
        "textColor": pal.ink,
        "borderColor": pal.line,
        "font": STREAMLIT_FONT,
        "codeFont": STREAMLIT_CODE_FONT,
    }


def main() -> None:
    print("[theme]")
    for option, value in streamlit_theme().items():
        print(f'{option} = "{value}"')


if __name__ == "__main__":
    main()
