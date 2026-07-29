import {
  Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { EquityPoint } from "../api/types";
import { palette } from "../theme";

interface Props {
  curve: EquityPoint[];
  benchmark?: EquityPoint[] | null;
  markers?: string[];
}

/** Both series share an x-axis of dates. The runs are computed over the same
 *  window from the same starting state, so the points align 1:1 by date and
 *  no rebasing is needed. */
export function EquityChart({ curve, benchmark, markers = [] }: Props) {
  const byDate = new Map(curve.map((p) => [p.date, { date: p.date, equity: p.equity }]));
  for (const p of benchmark ?? []) {
    const row = byDate.get(p.date) ?? { date: p.date, equity: Number.NaN };
    byDate.set(p.date, { ...row, benchmark: p.equity } as never);
  }
  const merged = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={merged} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <XAxis dataKey="date" stroke={palette.muted} tick={{ fontSize: 11 }} minTickGap={40} />
        <YAxis stroke={palette.muted} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip />
        <Line type="monotone" dataKey="equity" name="strategy" stroke={palette.series[0]}
          strokeWidth={2} dot={false} isAnimationActive={false} />
        {benchmark && (
          <Line type="monotone" dataKey="benchmark" name="buy-and-hold"
            stroke={palette.series[1]} strokeWidth={2} strokeDasharray="4 3"
            dot={false} isAnimationActive={false} />
        )}
        {markers.map((d) => (
          <ReferenceLine key={d} x={d} stroke={palette.muted} strokeDasharray="2 2" />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
