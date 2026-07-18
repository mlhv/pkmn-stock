# README revamp — recruiter-facing restructure

**Goal:** Restructure `README.md` so a recruiter skimming for 30-60 seconds
sees the engineering signals first (custom engine, bit-for-bit C++ parity,
18x walk-forward speedup, walk-forward rigor, CI), while keeping every
existing fact and the project's honest-negative-result identity intact.

**Decisions (user-approved):**
- Full restructure, not light polish; single README (no ENGINEERING.md split).
- Hero visual: a committed equity-curve PNG rendered from real local
  walk-forward artifacts by a new `scripts/render_readme_chart.py`.
- Framing: rigor as the hook — engineering first, then the negative result
  presented prominently as proof the methodology is honest.

## Structure (top to bottom)

1. Title + one-line tagline + badge row (~5): GitHub Actions CI (live),
   Python 3.12, C++20/nanobind, mypy strict, test counts (349 pytest +
   25 Catch2). Static badges via shields.io; no external services beyond
   the CI badge.
2. Hero chart: `docs/assets/oos_equity.png`, stitched
   out-of-sample equity curves — buy-and-hold sealed vs active strategies —
   caption states window (2024-08..2026-06) and cost regime (flat-cost,
   since that is the regime with stitched OOS curves for all strategies).
   Rendered by `scripts/render_readme_chart.py` from local
   `data/results/*/stitched_equity.parquet` artifacts (data/ is gitignored;
   the PNG is committed, the script makes it reproducible). Chart styling
   follows the dataviz skill.
3. "What this is": 4-5 bullets, engineering-led (event-driven engine, C++
   parity with exact `==` guarantee, 18x measured walk-forward speedup,
   walk-forward validation + overfitting gap, experiment registry).
4. Honest-result callout: blockquote immediately after the bullets — no
   active strategy beat buy-and-hold sealed once realistic costs are priced
   in; framed as the system being rigorous enough to say so; every number
   reproducible from its config hash.
5. Results table: current table preserved (column tweaks allowed, numbers
   untouched).
6. "Why the numbers are believable": same five points, tightened to ~1 line
   each.
7. Architecture: mermaid flowchart replacing the ASCII diagram; module
   paths preserved.
8. Engines: parity guarantee prose + both measured tables kept; build
   prerequisites trimmed.
9. Quickstart: fenced bash blocks with inline comments, ~10 core commands;
   launchd scheduling, paper-mode detail, and troubleshooting move into
   `<details>` blocks.
10. Engineering practices + future work: short bullets, as today.

## Style rules

- No em dashes; no AI-telltale phrasing (user's prose-style preference).
- Short paragraphs (max 2-3 sentences); GitHub-flavored markdown; HTML
  limited to badges/centering/`<details>`.
- Every current fact survives (re-homed or tightened, never dropped);
  no invented or oversold numbers — every figure must already exist in
  repo docs or measured output.
- Caveats stay: Sharpe/Sortino mark-smoothing caveat, stated limitations
  section content.

## Out of scope

- No changes to code behavior; `scripts/render_readme_chart.py` is the only
  new code and is read-only over artifacts.
- No docs/research-findings changes; no CLAUDE.md changes beyond, at most,
  a one-line pointer to the chart script.

## Acceptance

- README renders correctly on GitHub (mermaid, details blocks, badges,
  image path).
- All numbers cross-check against docs/research-findings-2026-07.md and
  the current README.
- `uv run pytest && ruff && mypy` gates stay green (chart script excluded
  from mypy scope like other scripts/, but ruff-clean).
