"""Convert every Word document of the exported packs to PDF, to look at them as rendered.

    .venv/bin/python scripts/render_packs.py [PACKS_DIR]

Needs LibreOffice (its `soffice` program), which is not a dependency of the tool: it is used
here only to check the documents. Writes the PDFs next to the packs, in `_pdf/`, with the
same folders, and `_pdf/pages.csv` with the number of pages of each document.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATES = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice")
HOW_TO_INSTALL = """LibreOffice was not found.

macOS: download the installer from the LibreOffice site (libreoffice.org, "Download"),
open the .dmg and drag LibreOffice into Applications. Open it once from Applications so
that macOS lets it run. Nothing else to set up: this script looks for
/Applications/LibreOffice.app/Contents/MacOS/soffice.
With Homebrew: brew install --cask libreoffice
Linux: sudo apt-get install libreoffice-writer (or your distribution's package).
Then run this script again."""


def find_soffice() -> str | None:
    for candidate in CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


def page_count(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes()))


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("exports/packs")
    if not root.is_dir():
        print(f"No such folder: {root}")
        return 1
    soffice = find_soffice()
    if soffice is None:
        print(HOW_TO_INSTALL)
        return 2
    out = root / "_pdf"
    documents = sorted(p for p in root.rglob("*.docx") if out not in p.parents)
    rows = []
    for folder in sorted({p.parent for p in documents}):
        target = out / folder.relative_to(root)
        target.mkdir(parents=True, exist_ok=True)
        batch = [str(p) for p in documents if p.parent == folder]
        done = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(target), *batch],
                              capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            print(done.stderr[-800:])
            return 1
    for document in documents:
        pdf = out / document.relative_to(root).with_suffix(".pdf")
        rows.append((str(document.relative_to(root)), page_count(pdf) if pdf.exists() else "NOT CONVERTED"))
    with (out / "pages.csv").open("w", newline="") as handle:
        csv.writer(handle).writerows([("document", "pages"), *rows])
    missing = [name for name, pages in rows if pages == "NOT CONVERTED"]
    print(f"{len(rows) - len(missing)} of {len(rows)} documents converted to {out}")
    for name in missing:
        print(f"  not converted: {name}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
