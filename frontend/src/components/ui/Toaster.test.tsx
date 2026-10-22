import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Toaster } from './Toaster';
import { showToast } from './toast';

// vitest has no globals (project convention): RTL's auto cleanup does not
// run, so clean up explicitly. The toast store is module-level state shared
// by all tests in this file — the empty-queue case must run first (before
// anything is pushed), and showToast must be wrapped in act() because its
// emit() is an external (non-act) state update.
afterEach(() => { cleanup(); });

describe('Toaster (audit 2026-10-20 P1-1: showToast had no consumer)', () => {
  it('renders nothing while the queue is empty', () => {
    const { container } = render(<Toaster />);
    expect(container.firstChild).toBeNull();
  });

  it('renders toasts pushed by showToast', () => {
    render(<Toaster />);
    act(() => {
      showToast('配置已保存', 'info');
      showToast('Stop timed out: the process may still be running', 'error');
    });
    expect(screen.getByText('配置已保存')).toBeTruthy();
    expect(screen.getByText('Stop timed out: the process may still be running')).toBeTruthy();
  });

  it('dismisses a toast on click and keeps the others', () => {
    render(<Toaster />);
    act(() => {
      showToast('第一条');
      showToast('第二条');
    });
    // fireEvent (not the native .click()): RTL's fireEvent is wrapped in
    // act, so the dismiss state update flushes before the assertion.
    fireEvent.click(screen.getByText('第一条'));
    expect(screen.queryByText('第一条')).toBeNull();
    expect(screen.getByText('第二条')).toBeTruthy();
  });

  it('renders above the Modal overlay (audit 2026-10-22 FE-4: z-50 sat below z-[95])', () => {
    render(<Toaster />);
    act(() => {
      showToast('层级检查');
    });
    const layer = screen.getByText('层级检查').closest('div.fixed') as HTMLElement;
    expect(layer.className).toContain('z-[100]');
  });
});
