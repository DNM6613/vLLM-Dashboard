import { useEffect, useRef, useState } from 'react';

export function useAnimatedNumber(value: number, duration = 600): number {
  const [display, setDisplay] = useState(value);
  const displayRef = useRef(value);
  const rafRef = useRef(0);

  useEffect(() => {
    const from = displayRef.current;
    if (from === value) {
      return;
    }
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - p) * (1 - p);
      const next = from + (value - from) * eased;
      displayRef.current = next;
      setDisplay(next);
      if (p < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value, duration]);

  return display;
}
