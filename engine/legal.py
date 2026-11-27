"""Legal references, for orientation only (config/legal.yaml, docs/LEGAL_REFERENCES.md).

Pure functions. They turn the concepts of the tool (a decision, an option, a stage, a line of
the resolution summary, a part of a document) into the "Reference:" line shown next to it.
Every reference comes from config/legal.yaml; a concept with no row there gets nothing, and
with the switch off nothing is shown at all. The tool models none of the legal conditions.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from engine.schema import LegalReferences, ScriptConfig

PREFIX = "Reference: "


def active(legal: Optional[LegalReferences]) -> Optional[LegalReferences]:
    """The references to show, or None when there are none or the switch is off."""
    return legal if legal is not None and legal.show else None


def of(script: Optional[ScriptConfig]) -> Optional[LegalReferences]:
    return active(script.legal) if script is not None else None


def with_switch(script: ScriptConfig, show: bool) -> ScriptConfig:
    """The script with the references switched on or off."""
    if script.legal is None or script.legal.show == show:
        return script
    return script.model_copy(update={"legal": script.legal.model_copy(update={"show": show})})


def shown(legal: LegalReferences, key: str) -> str:
    """One row as shown: its reference, and the mark of a status still to be confirmed."""
    row = legal.references[key]
    if row.status == "B":
        return f"{row.reference} (to be confirmed)"
    if row.status.startswith("A for the article"):
        return f"{row.reference} (paragraph to be confirmed)"
    return row.reference


def keys(legal: Optional[LegalReferences], concepts: Iterable[str]) -> tuple[str, ...]:
    """The rows that label the given concepts, each once, in order."""
    if active(legal) is None:
        return ()
    out: list[str] = []
    for concept in concepts:
        for key in legal.concepts.get(concept, ()):
            if key not in out:
                out.append(key)
    return tuple(out)


def line(legal: Optional[LegalReferences], concepts: Iterable[str]) -> str:
    """The "Reference:" line for the concepts, or "" if none of them has a row."""
    found = keys(legal, concepts)
    return PREFIX + "; ".join(shown(legal, key) for key in found) if found else ""


def figure_concepts(labels: Iterable[str], legal: Optional[LegalReferences]) -> list[str]:
    """The concepts of the lines of the resolution summary, matched on their first words."""
    if active(legal) is None:
        return []
    prefixes = [c.split(":", 1)[1] for c in legal.concepts if c.startswith("figure:")]
    out = []
    for label in labels:
        for prefix in prefixes:
            if label.startswith(prefix):
                out.append(f"figure:{prefix}")
    return out


def decision_concepts(decision: str, options: Sequence[str] = ()) -> list[str]:
    return [f"decision:{decision}", *(f"option:{decision}:{option}" for option in options)]


def inject_concepts(inject, legal: Optional[LegalReferences]) -> list[str]:
    """The concepts an inject is about: the stages it notifies, the decision it opens or
    reports, the lines of the resolution summary it carries."""
    out = [f"stage:{stage}" for stage in (*inject.stages, inject.kind)]
    if inject.decision_due is not None:
        due = inject.decision_due
        out += decision_concepts(due.decision, [o.option for o in due.options])
    if getattr(inject, "decision", ""):
        out.append(f"decision:{inject.decision}")
    out += figure_concepts((figure.label for figure in inject.resolution), legal)
    return out


def inject_line(inject, legal: Optional[LegalReferences]) -> str:
    return line(legal, inject_concepts(inject, legal))


def table(legal: LegalReferences) -> list[tuple[str, str, str]]:
    """The whole table: (concept, reference, status), as in the file."""
    return [(row.concept, row.reference, row.status) for row in legal.references.values()]
