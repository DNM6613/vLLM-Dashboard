import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
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
