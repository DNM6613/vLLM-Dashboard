import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'danger';
type Size = 'sm' | 'md';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent/20 text-accent hover:bg-accent/30',
  secondary: 'bg-bg-hover text-text',
  danger: 'bg-danger text-white hover:bg-danger/90',
};

const SIZES: Record<Size, string> = {
  sm: 'px-2 py-0.5 text-xs',
  md: 'px-4 py-2 text-sm',
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export function Button({ variant = 'secondary', size = 'md', className = '', ...rest }: ButtonProps) {
  return (
    <button
      className={`rounded-lg flex items-center gap-1 transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    />
  );
}
