import { useState } from "react";
import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import type { StrategyStat } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { useAsync } from "../hooks/useAsync";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;
const dsrText = (d: number | null) => (d === null ? "n/a" : d.toFixed(3));

export function RigorCompare() {
  const { runId = "" } = useParams();
  const { loading, error, data } = useAsync(() => apiClient.getEvaluate(runId), [runId]);
  const [byDsr, setByDsr] = useState(false);
  return (
    <section>
      <AsyncState loading={loading} error={error} data={data}>
        {(ev) => {
          const rows: StrategyStat[] = byDsr
            ? [...ev.strategies].sort((a, b) => (b.dsr ?? -1) - (a.dsr ?? -1))
            : ev.strategies;
          return (
            <>
              <h2>Rigor comparison</h2>
              <p>
                <strong>White&apos;s Reality Check</strong> (best vs benchmark, jointly over{" "}
                {ev.strategies.length} strategies): p = {ev.reality_check_p.toFixed(4)}
              </p>
              <p>{ev.n_days} aligned days ({ev.start} .. {ev.end}), benchmark{" "}
                <code>{ev.benchmark.split("/").pop()}</code>.</p>
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th><th>OOS return</th><th>95% CI</th><th>Sharpe</th>
                    <th><button type="button" onClick={() => setByDsr(true)}>
                      Deflated Sharpe</button></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.strategy} data-testid="strategy-row">
                      <td>{s.strategy}</td>
                      <td>{pct(s.total_return)}</td>
                      <td>[{pct(s.ci.lo)}, {pct(s.ci.hi)}]</td>
                      <td>{s.sharpe.toFixed(2)}</td>
                      <td>{dsrText(s.dsr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p><em>Sharpe-derived figures (Sharpe, deflated Sharpe, CIs) are inflated by
                mark smoothing; treat them as optimistic.</em></p>
            </>
          );
        }}
      </AsyncState>
    </section>
  );
}
