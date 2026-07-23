import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import type { RunSummary } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { useAsync } from "../hooks/useAsync";

const DETAIL_PATH: Record<string, string> = {
  walkforward: "walkforward",
  evaluate: "evaluate",
};

function headline(r: RunSummary): string {
  const [k, v] = Object.entries(r.results)[0] ?? ["", 0];
  return k ? `${k}: ${v}` : "-";
}

export function RunsBrowser() {
  const { loading, error, data } = useAsync(() => apiClient.listRuns());
  return (
    <section>
      <h2>Runs</h2>
      <AsyncState loading={loading} error={error} data={data} empty={(d) => d.length === 0}>
        {(runs) => (
          <table>
            <thead>
              <tr><th>Run</th><th>Command</th><th>Strategy</th><th>Result</th><th>Recorded</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const seg = DETAIL_PATH[r.command];
                return (
                  <tr key={r.run_id}>
                    <td>
                      {seg
                        ? <Link to={`/${seg}/${r.run_id}`}>{r.run_id}</Link>
                        : r.run_id}
                    </td>
                    <td>{r.command}</td>
                    <td>{r.strategy}</td>
                    <td>{headline(r)}</td>
                    <td>{r.recorded_at.slice(0, 10)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </AsyncState>
    </section>
  );
}
