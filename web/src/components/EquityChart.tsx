import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityPoint, RigorCI } from "../api/types";
import { palette } from "../theme";

export function EquityChart({ curve }: { curve: EquityPoint[]; rigor: RigorCI | null }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={curve} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <XAxis dataKey="date" stroke={palette.muted} tick={{ fontSize: 11 }} minTickGap={40} />
        <YAxis stroke={palette.muted} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
        <Tooltip />
        <Line type="monotone" dataKey="equity" stroke={palette.series[0]} strokeWidth={2}
          dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
