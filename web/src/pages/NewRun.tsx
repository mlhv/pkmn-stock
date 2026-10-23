import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";
import type { StrategyInfo } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { HoldingsEditor, type HoldingRow } from "../components/HoldingsEditor";
import { useAsync } from "../hooks/useAsync";

export function NewRun() {
  const { loading, error, data } = useAsync(() => apiClient.getStrategies());
  const navigate = useNavigate();
  const [strategy, setStrategy] = useState("buy-and-hold");
  const [params, setParams] = useState<Record<string, number>>({});
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState(10000);
  const [impact, setImpact] = useState(true);
  const [execution, setExecution] = useState<"book" | "formula">("book");
  const [kind, setKind] = useState("sealed");
  const [holdings, setHoldings] = useState<HoldingRow[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const submit = async (specs: StrategyInfo[]) => {
    setSubmitError(null);
    const current = specs.find((s) => s.name === strategy);
    const filled = Object.fromEntries(
      (current?.params ?? []).map((p) => [p.name, params[p.name] ?? p.default]),
    );
    try {
      await apiClient.triggerBacktest({
        strategy, start, end, params: filled, cash, impact, execution, kind,
        holdings: holdings.map(({ product_id, sub_type, quantity, avg_cost, opened_on }) =>
          ({ product_id, sub_type, quantity, avg_cost, opened_on })),
      });
      navigate("/jobs");
    } catch (e) {
      setSubmitError((e as Error).message);
    }
  };

  return (
    <section>
      <h2>New backtest</h2>
      <AsyncState loading={loading} error={error} data={data}>
        {(specs) => {
          const current = specs.find((s) => s.name === strategy);
          return (
            <form onSubmit={(e) => { e.preventDefault(); void submit(specs); }}>
              <label htmlFor="strategy">Strategy</label>
              <select id="strategy" value={strategy}
                onChange={(e) => { setStrategy(e.target.value); setParams({}); }}>
                {specs.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
              <p>{current?.thesis}</p>

              <fieldset>
                <legend>Parameters</legend>
                {current && current.params.length === 0
                  ? <p>This strategy has no tunable parameters.</p>
                  : current?.params.map((p) => (
                    <div key={p.name}>
                      <label htmlFor={`p-${p.name}`}>{p.name}</label>
                      <input id={`p-${p.name}`} aria-label={p.name} type="number"
                        min={p.low} max={p.high} step={p.kind === "int" ? 1 : "any"}
                        placeholder={String(p.default)}
                        value={params[p.name] ?? ""}
                        onChange={(e) => setParams({
                          ...params,
                          [p.name]: Number(e.target.value),
                        })} />
                      <small>{p.low} – {p.high} (default {p.default})</small>
                    </div>
                  ))}
              </fieldset>

              <label htmlFor="start">Start</label>
              <input id="start" type="date" value={start}
                onChange={(e) => setStart(e.target.value)} />
              <label htmlFor="end">End</label>
              <input id="end" type="date" value={end}
                onChange={(e) => setEnd(e.target.value)} />
              <label htmlFor="cash">Starting cash</label>
              <input id="cash" type="number" min={0} value={cash}
                onChange={(e) => setCash(Number(e.target.value))} />
              <label htmlFor="impact">
                <input id="impact" type="checkbox" checked={impact}
                  onChange={(e) => setImpact(e.target.checked)} />
                Walk-the-spread market impact
              </label>
              <label htmlFor="execution">Execution</label>
              <select id="execution" value={execution}
                onChange={(e) => setExecution(e.target.value as "book" | "formula")}>
                <option value="book">book (modelled resting orders)</option>
                <option value="formula">formula</option>
              </select>
              {strategy === "buy-and-hold" && (
                <>
                  <label htmlFor="kind">Universe</label>
                  <select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
                    <option value="sealed">sealed</option>
                    <option value="single">single</option>
                  </select>
                </>
              )}

              <HoldingsEditor value={holdings} onChange={setHoldings} start={start} />

              {submitError && <p role="alert">{submitError}</p>}
              <button type="submit">Run backtest</button>
            </form>
          );
        }}
      </AsyncState>
    </section>
  );
}
