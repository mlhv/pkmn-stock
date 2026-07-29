import { useEffect, useState } from "react";
import { apiClient } from "../api/client";
import type { HoldingInput, ProductHit } from "../api/types";

interface Row extends HoldingInput { name: string; sub_types: string[]; last_priced_on: string; }

export function HoldingsEditor(
  { value, onChange, start }: {
    value: Row[];
    onChange: (rows: Row[]) => void;
    start: string;
  },
) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ProductHit[]>([]);

  useEffect(() => {
    if (query.trim().length < 2) { setHits([]); return; }
    let alive = true;
    const t = setTimeout(() => {
      apiClient.searchProducts(query).then(
        (h) => alive && setHits(h),
        () => alive && setHits([]),
      );
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [query]);

  const add = (p: ProductHit) => {
    onChange([...value, {
      product_id: p.product_id, sub_type: p.sub_types[0] ?? "Normal",
      // Default to the window start, not today: every backtest window is
      // historical, so today is always after it — which the API rejects, and
      // which used to silently disarm every duration-gated strategy.
      quantity: 1, avg_cost: 0,
      opened_on: start || new Date().toISOString().slice(0, 10),
      name: p.name, sub_types: p.sub_types, last_priced_on: p.last_priced_on,
    }]);
    setQuery("");
    setHits([]);
  };

  const patch = (i: number, fields: Partial<Row>) =>
    onChange(value.map((r, j) => (j === i ? { ...r, ...fields } : r)));

  return (
    <fieldset>
      <legend>Starting holdings</legend>
      <label htmlFor="product-search">Product search</label>
      <input id="product-search" value={query} onChange={(e) => setQuery(e.target.value)}
        placeholder="Search the card catalog…" />
      {hits.length > 0 && (
        <ul>
          {hits.map((p) => (
            <li key={p.product_id}>
              <button type="button" onClick={() => add(p)}>
                {p.name} <small>({p.kind}, last priced {p.last_priced_on})</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {value.map((r, i) => (
        <div key={`${r.product_id}-${i}`}>
          <input aria-label="Product" readOnly value={r.name} />
          <label htmlFor={`sub-${i}`}>Sub type</label>
          <select id={`sub-${i}`} aria-label="Sub type" value={r.sub_type}
            onChange={(e) => patch(i, { sub_type: e.target.value })}>
            {r.sub_types.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <input aria-label="Quantity" type="number" min={1} value={r.quantity}
            onChange={(e) => patch(i, { quantity: Number(e.target.value) })} />
          <input aria-label="Avg cost" type="number" min={0} step="0.01" value={r.avg_cost}
            onChange={(e) => patch(i, { avg_cost: Number(e.target.value) })} />
          <input aria-label="Opened on" type="date" value={r.opened_on}
            onChange={(e) => patch(i, { opened_on: e.target.value })} />
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
    </fieldset>
  );
}

export type { Row as HoldingRow };
