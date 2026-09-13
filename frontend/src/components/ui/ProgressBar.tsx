interface ProgressBarProps {
  value: number;
  color?: string;
  className?: string;
}

export function ProgressBar({ value, color = 'bg-accent', className = '' }: ProgressBarProps) {
  return (
    <div className={`h-1 bg-bg rounded-full overflow-hidden ${className}`}>
      <div
        className={`h-full rounded-full ${color} transition-[width] duration-[800ms] ease-linear`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}
