import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ErrorBoundary } from './ErrorBoundary';

function Boom(): never {
  throw new Error('render exploded');
}

// vitest has no globals (project convention): RTL's auto cleanup does not
// run, so clean up explicitly.
afterEach(() => { cleanup(); });

describe('ErrorBoundary (audit 2026-10-20 P2-17: a render crash used to blank the whole page)', () => {
  it('renders children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <div>page content</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('page content')).toBeTruthy();
  });

  it('shows the fallback with a reload button when a child throws', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/Page failed to render/)).toBeTruthy();
    expect(screen.getByText('render exploded')).toBeTruthy();
    const btn = screen.getByRole('button', { name: /Reload/ });
    expect(btn).toBeTruthy();
    // jsdom cannot redefine location.reload (non-configurable), so the click
    // is asserted to exercise the onClick path (window.location.reload)
    // without throwing instead of the navigation itself.
    fireEvent.click(btn);
    expect(screen.getByText(/Page failed to render/)).toBeTruthy();
    errSpy.mockRestore();
  });
});
