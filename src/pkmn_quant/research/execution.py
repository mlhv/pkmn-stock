"""How every surface names a run's execution mode.

Book execution fills against modelled resting orders, not real ones, so every
place book results appear (CLI output, the markdown reports, the dashboard and
the web explorer) carries the synthetic label below. A missing mode on a
record or artifact is "formula": every one written before Plan B was.
"""

from __future__ import annotations

BOOK_LABEL = "book (modelled resting orders, synthetic, built from the day's tcgcsv quote)"


def execution_line(mode: str) -> str:
    """The one-line mode statement: ``Execution: book (...)`` or ``Execution: formula``."""
    return f"Execution: {BOOK_LABEL}" if mode == "book" else f"Execution: {mode}"
