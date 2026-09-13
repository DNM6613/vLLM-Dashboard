import { useAnimatedNumber } from '../../hooks/useAnimatedNumber';

interface AnimatedNumberProps {
  value: number;
  format?: (v: number) => string;
  className?: string;
  duration?: number;
}

export function AnimatedNumber({
  value,
  format = (v) => String(Math.round(v)),
  className,
  duration,
}: AnimatedNumberProps) {
  const display = useAnimatedNumber(value, duration);
  return <span className={className}>{format(display)}</span>;
}
