"""MuPDF message handling — keep the C library off the console, in the catalog.

MuPDF reports malformed PDFs through two separate channels, and PyMuPDF wires
them up differently:

  * **warnings** are buffered only (``mupdf_display_warnings()`` is 0 by
    default), retrievable via ``fitz.TOOLS.mupdf_warnings()``;
  * **errors** are ALSO buffered, but by default are additionally written
    straight to **file descriptor 1 (stdout)** by the C library — not stderr,
    and not through Python's logging or warnings machinery at all.

That last point is why lines like

    MuPDF error: format error: No common ancestor in structure tree

used to appear in the console window with no indication of which file caused
them, and why no amount of Python-side exception handling caught them: nothing
was raised. The file still opens — MuPDF recovers — so these are diagnostics
about a defective source PDF, not failures.

Turning the display off loses nothing: the text stays in the same buffer either
way (verified against PyMuPDF 1.27.2). So this module silences the console
channel at import and hands the messages back through `drain_pdf_messages`,
which the prescreener folds into the file's ``format_issues`` — attributing the
defect to a filename, and carrying it into media.csv.
"""

import fitz  # pymupdf

# Process-global C-library state, so it is set once at import. Every module
# that opens a PDF imports this one, which makes that unconditional.
fitz.TOOLS.mupdf_display_errors(False)

# MuPDF's own prefixes. Stripped so the catalog reads as a description of the
# file ("PDF structure: no common ancestor…") rather than as a stack of library
# jargon, and so the same defect is worded identically however it is reached.
_STRIP_PREFIXES = ("format error:", "error:", "warning:", "syntax error:")

# Progress chatter, not defects — MuPDF narrates its own recovery. Reporting
# these as problems with the file would be noise of a different kind.
_IGNORED = (
    "trying to repair",
    "repairing pdf document",
    "repaired",
    # MuPDF collapses a repeated complaint into a marker line of its own
    # ("... repeated 29 times..."). It describes the log, not the file, and it
    # is meaningless once duplicates are collapsed here anyway.
    "... repeated",
)


def _clean(line: str) -> str:
    text = line.strip()
    lowered = text.lower()
    for prefix in _STRIP_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def drain_pdf_messages() -> list:
    """Return MuPDF's buffered messages for the work just done, and clear it.

    Always drains, so a message can never leak into the next file's results —
    the buffer is global and cumulative. Returns human-readable strings with
    MuPDF's prefixes removed, recovery chatter dropped, and duplicates
    collapsed (MuPDF repeats a complaint once per affected object).
    """
    raw = fitz.TOOLS.mupdf_warnings(reset=True) or ""
    seen = []
    for line in raw.splitlines():
        text = _clean(line)
        if not text or text.lower().startswith(_IGNORED):
            continue
        if text not in seen:
            seen.append(text)
    return seen


def describe_pdf_defects() -> list:
    """Drain the buffer and phrase each message as a `format_issues` entry."""
    return [f"PDF structure: {msg}" for msg in drain_pdf_messages()]
