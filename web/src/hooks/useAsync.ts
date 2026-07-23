import { useEffect, useState } from "react";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: T | null }>(
    { loading: true, error: null, data: null },
  );
  useEffect(() => {
    let alive = true;
    setState({ loading: true, error: null, data: null });
    fn().then(
      (data) => alive && setState({ loading: false, error: null, data }),
      (e: Error) => alive && setState({ loading: false, error: e.message, data: null }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
