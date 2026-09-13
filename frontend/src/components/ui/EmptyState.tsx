import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  className?: string;
}

export function EmptyState({ icon, title, description, className = '' }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center text-center text-text-muted ${className}`}>
      {icon}
      <p className="text-sm">{title}</p>
      {description && <p className="text-xs mt-1">{description}</p>}
    </div>
  );
}
