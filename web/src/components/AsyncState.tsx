import type { ReactNode } from "react";

interface Props<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
  empty?: (d: T) => boolean;
  children: (d: T) => ReactNode;
}

export function AsyncState<T>({ loading, error, data, empty, children }: Props<T>) {
  if (loading) return <p role="status">Loading…</p>;
  if (error) return <p role="alert">Error: {error}</p>;
  if (!data || (empty && empty(data))) return <p>Nothing to show yet.</p>;
  return <>{children(data)}</>;
}
