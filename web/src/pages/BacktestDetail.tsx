import { useState } from "react";
import { useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import { AsyncState } from "../components/AsyncState";
import { EquityChart } from "../components/EquityChart";
import { useAsync } from "../hooks/useAsync";

const PAGE = 100;

export function BacktestDetail() {
  const { runId = "" } = useParams();
  const detail = useAsync(() => apiClient.getBacktest(runId), [runId]);
  const [offset, setOffset] = useState(0);
  const fills = useAsync(() => apiClient.getFills(runId, offset, PAGE), [runId, offset]);

  return (
    <section>
      <h2>Backtest {runId}</h2>
      <AsyncState loading={detail.loading} error={detail.error} data={detail.data}>
        {(d) => (
          <>
            <dl>
              {Object.entries(d.summary).map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v.toFixed(4)}</dd></div>
              ))}
            </dl>
            <EquityChart curve={d.equity_curve} benchmark={d.benchmark?.equity_curve ?? null}
              markers={d.seeded_exits} />
            {d.benchmark
              ? <p>Benchmark: {d.benchmark.strategy} ({d.benchmark.run_id})</p>
              : <p>No benchmark paired with this run.</p>}
            <h3>Configuration</h3>
            <pre>{JSON.stringify(d.config, null, 2)}</pre>
            <h3>Fills ({d.n_fills})</h3>
            <AsyncState loading={fills.loading} error={fills.error} data={fills.data}
              empty={(f) => f.n_fills === 0}>
              {(page) => (
                <>
                  <table>
                    <thead>
                      <tr>
                        <th>Day</th><th>Product</th><th>Sub type</th><th>Qty</th>
                        <th>Price</th><th>Fees</th><th>Impact</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {page.fills.map((f, i) => (
                        <tr key={`${f.day}-${f.product_id}-${i}`}>
                          <td>{f.day}</td><td>{f.product_id}</td><td>{f.sub_type}</td>
                          <td>{f.quantity}</td><td>{f.price.toFixed(2)}</td>
                          <td>{f.fees.toFixed(2)}</td><td>{f.impact.toFixed(2)}</td>
                          <td>{f.seeded ? "seeded" : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <button type="button" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                  <button type="button" disabled={page.fills.length === 0}
                    onClick={() => setOffset(offset + PAGE)}>Next</button>
                </>
              )}
            </AsyncState>
          </>
        )}
      </AsyncState>
    </section>
  );
}
