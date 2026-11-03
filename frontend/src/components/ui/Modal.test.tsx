import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { Modal } from './Modal';

// vitest has no globals (project convention): RTL's auto cleanup does not
// run, so clean up explicitly.
afterEach(() => { cleanup(); });

// The Escape handler is a native document-level capture listener, so a
// plain fireEvent.keyDown on the document reaches every mounted modal.
function pressEscape(): void {
  fireEvent.keyDown(document, { key: 'Escape' });
}

describe('Modal Escape stacking (audit 2026-10-22 FE-1)', () => {
  it('a single modal closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <I18nProvider>
        <Modal title="M" onClose={onClose}>body</Modal>
      </I18nProvider>,
    );
    pressEscape();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('only the top-most (last in DOM) of sibling modals reacts to Escape', () => {
    const first = vi.fn();
    const second = vi.fn();
    render(
      <I18nProvider>
        <>
          <Modal title="A" onClose={first}>a</Modal>
          <Modal title="B" onClose={second}>b</Modal>
        </>
      </I18nProvider>,
    );
    pressEscape();
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it('a modal nested inside another modal owns Escape (last in DOM)', () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(
      <I18nProvider>
        <Modal title="Outer" onClose={outer}>
          <Modal title="Inner" onClose={inner}>deep</Modal>
        </Modal>
      </I18nProvider>,
    );
    pressEscape();
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });

  it('after the top-most modal unmounts, Escape reaches the next one down', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(
      <I18nProvider>
        <>
          <Modal title="A" onClose={first}>a</Modal>
          <Modal title="B" onClose={second}>b</Modal>
        </>
      </I18nProvider>,
    );
    rerender(
      <I18nProvider>
        <Modal title="A" onClose={first}>a</Modal>
      </I18nProvider>,
    );
    pressEscape();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });
});

describe('Modal backdrop (nested deploy modal on server config)', () => {
  it('renders the dark backdrop by default', () => {
    render(
      <I18nProvider>
        <Modal title="M" onClose={() => {}}>body</Modal>
      </I18nProvider>,
    );
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement;
    expect(overlay.className).toContain('bg-black/60');
    expect(overlay.className).toContain('backdrop-blur-sm');
  });

  it('skips the dark backdrop with backdrop={false} but keeps the positioning overlay', () => {
    const onClose = vi.fn();
    render(
      <I18nProvider>
        <Modal title="M" onClose={onClose} backdrop={false}>body</Modal>
      </I18nProvider>,
    );
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement;
    expect(overlay.className).not.toContain('bg-black/60');
    expect(overlay.className).not.toContain('backdrop-blur-sm');
    expect(overlay.className).toContain('fixed');
    expect(overlay.className).toContain('inset-0');
    expect(overlay.className).toContain('z-[95]');
    // The invisible overlay still swallows the click (blocks the modal
    // underneath) but does not close the panel.
    fireEvent.mouseDown(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not close on outside click (user ruling 2026-11-03: all panels)', () => {
    const onClose = vi.fn();
    render(
      <I18nProvider>
        <Modal title="M" onClose={onClose}>body</Modal>
      </I18nProvider>,
    );
    const overlay = screen.getByRole('dialog').parentElement as HTMLElement;
    fireEvent.mouseDown(overlay);
    expect(onClose).not.toHaveBeenCalled();
  });
});
