import { useEffect, useRef, useState } from "react";

/** Fetch immediately, then re-fetch every `intervalMs` for as long as
 *  `shouldContinue(data)` holds. Stops on unmount and when work goes idle,
 *  so an idle jobs page makes no requests. */
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  shouldContinue: (data: T) => boolean,
) {
  const [state, setState] = useState<{ loading: boolean; error: string | null; data: T | null }>(
    { loading: true, error: null, data: null },
  );
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const keepRef = useRef(shouldContinue);
  keepRef.current = shouldContinue;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      fnRef.current().then(
        (data) => {
          if (!alive) return;
          setState({ loading: false, error: null, data });
          if (keepRef.current(data)) timer = setTimeout(tick, intervalMs);
        },
        (e: Error) => alive && setState({ loading: false, error: e.message, data: null }),
      );
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [intervalMs]);

  return state;
}
