import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import { AsyncState } from "../components/AsyncState";
import { EquityChart } from "../components/EquityChart";
import { useAsync } from "../hooks/useAsync";

const pct = (x: number) => `${(x * 100).toFixed(2)}%`;

export function WalkForwardDetail() {
  const { runId = "" } = useParams();
  const { loading, error, data } = useAsync(() => apiClient.getWalkforward(runId), [runId]);
  return (
    <section>
      <AsyncState loading={loading} error={error} data={data}>
        {(wf) => (
          <>
            <h2>Walk-forward: {wf.strategy}</h2>
            <EquityChart curve={wf.equity_curve} rigor={wf.rigor} />
            {wf.rigor && (
              <p>
                Stitched OOS total return {pct(wf.rigor.point)}, {" "}
                {(wf.rigor.level * 100).toFixed(0)}% CI [{pct(wf.rigor.lo)}, {pct(wf.rigor.hi)}]
                {" "}(block bootstrap, n_boot={wf.rigor.n_boot}, seed {wf.rigor.seed})
              </p>
            )}
            <p><em>Sharpe-derived figures are inflated by mark smoothing; treat bands as
              optimistic.</em></p>
            <h3>Summary</h3>
            <ul>{Object.entries(wf.summary).map(([k, v]) =>
              <li key={k}>{k}: {v.toFixed(4)}</li>)}</ul>
            <h3>Folds</h3>
            <table>
              <thead><tr><th>IS</th><th>OOS</th><th>params</th><th>IS ret</th><th>OOS ret</th>
              </tr></thead>
              <tbody>
                {wf.folds.map((f, i) => (
                  <tr key={i}>
                    <td>{f.is_start} .. {f.is_end}</td>
                    <td>{f.oos_start} .. {f.oos_end}</td>
                    <td>{Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(", ")}</td>
                    <td>{pct(f.is_summary.total_return ?? 0)}</td>
                    <td>{pct(f.oos_summary.total_return ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </AsyncState>
    </section>
  );
}
