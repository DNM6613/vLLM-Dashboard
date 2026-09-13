import { useEffect, useState } from 'react';

export function useSettledFlag(value: boolean, delayMs: number): boolean {
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    if (!value) {
      setSettled(false);
      return;
    }
    const timer = setTimeout(() => setSettled(true), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return settled;
}
