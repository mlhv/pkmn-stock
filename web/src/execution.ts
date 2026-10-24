// How the web explorer names a run's execution mode. Book results are fills
// against modelled resting orders (synthetic, built from the day's tcgcsv
// quote), not real ones, so every screen that shows them says so.
// A missing mode reads as formula, the same rule as the API.
export function executionLabel(mode: string | undefined): string {
  return mode === "book" ? "book (modelled, synthetic)" : (mode || "formula");
}
