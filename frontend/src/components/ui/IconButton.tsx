import type { ButtonHTMLAttributes, ReactNode } from 'react';

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> {
  ariaLabel: string;
  size?: 'sm' | 'md' | 'xs';
  children: ReactNode;
}

export function IconButton({ ariaLabel, size = 'md', className = '', title, children, ...rest }: IconButtonProps) {
  return (
    <button
      aria-label={ariaLabel}
      title={title ?? ariaLabel}
      className={`rounded-lg hover:bg-bg-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${size === 'xs' ? 'p-0.5' : size === 'sm' ? 'p-1.5' : 'p-2'} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
