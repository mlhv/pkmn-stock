import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import type { JobStatus } from "../api/types";
import { AsyncState } from "../components/AsyncState";
import { usePolling } from "../hooks/usePolling";

const ACTIVE = new Set(["queued", "running"]);

export function JobsView() {
  const { loading, error, data } = usePolling(
    () => apiClient.listJobs(),
    1000,
    (jobs: JobStatus[]) => jobs.some((j) => ACTIVE.has(j.status)),
  );
  return (
    <section>
      <h2>Jobs</h2>
      <AsyncState loading={loading} error={error} data={data} empty={(d) => d.length === 0}>
        {(jobs) => (
          <table>
            <thead>
              <tr><th>Job</th><th>Strategy</th><th>Status</th><th>Result</th><th>Submitted</th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.job_id}</td>
                  <td>{String(j.request.strategy ?? "-")}</td>
                  <td>{j.status}</td>
                  <td>
                    {j.run_id
                      ? <Link to={`/backtest/${j.run_id}`}>{j.run_id}</Link>
                      : j.error
                        ? <span role="alert">{j.error}</span>
                        : "-"}
                  </td>
                  <td>{j.submitted_at.slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncState>
    </section>
  );
}
