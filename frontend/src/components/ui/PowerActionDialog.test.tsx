import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { PowerActionDialog } from './PowerActionDialog';

// vitest has no globals (project convention): RTL's auto cleanup does not
// run, so clean up explicitly.
afterEach(() => { cleanup(); });

function renderDialog(onConfirm = vi.fn(), onCancel = vi.fn()) {
  const utils = render(
    <I18nProvider>
      <PowerActionDialog onConfirm={onConfirm} onCancel={onCancel} />
    </I18nProvider>,
  );
  return { utils, onConfirm, onCancel };
}

// The radio's accessible name starts with the verb (mono label); anchor the
// match so e.g. /off/ does not hit the "power off" in the cycle row.
function radio(action: string) {
  return screen.getByRole('radio', { name: new RegExp(`^${action}\\b`) });
}

describe('PowerActionDialog', () => {
  it('lists all four chassis actions with soft selected by default', () => {
    renderDialog();
    for (const action of ['off', 'soft', 'reset', 'cycle']) {
      expect(radio(action).getAttribute('aria-checked'))
        .toBe(action === 'soft' ? 'true' : 'false');
    }
  });

  it('honours the defaultAction prop', () => {
    render(
      <I18nProvider>
        <PowerActionDialog defaultAction="cycle" onConfirm={vi.fn()} onCancel={vi.fn()} />
      </I18nProvider>,
    );
    expect(radio('cycle').getAttribute('aria-checked')).toBe('true');
  });

  it('selecting an action and confirming passes it to onConfirm', () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(radio('reset'));
    fireEvent.click(screen.getByRole('button', { name: 'reset' }));
    expect(onConfirm).toHaveBeenCalledWith('reset');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('the confirm button tracks the selected action', () => {
    const { onConfirm } = renderDialog();
    // default soft
    fireEvent.click(screen.getByRole('button', { name: 'soft' }));
    expect(onConfirm).toHaveBeenCalledWith('soft');
    // switch to hard off, then confirm again
    fireEvent.click(radio('off'));
    fireEvent.click(screen.getByRole('button', { name: 'off' }));
    expect(onConfirm).toHaveBeenLastCalledWith('off');
  });

  it('cancels without confirming', () => {
    const { onConfirm, onCancel } = renderDialog();
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  describe('without a BMC (bmcConfigured=false)', () => {
    function renderNoBmc(onConfirm = vi.fn(), onCancel = vi.fn()) {
      const utils = render(
        <I18nProvider>
          <PowerActionDialog bmcConfigured={false} onConfirm={onConfirm} onCancel={onCancel} />
        </I18nProvider>,
      );
      return { utils, onConfirm, onCancel };
    }

    it('lists the SSH fallback set and disables the IPMI-only verbs', () => {
      renderNoBmc();
      // off/reset/cycle are IPMI-only -> disabled; soft + reboot enabled.
      for (const a of ['off', 'reset', 'cycle']) {
        const el = radio(a);
        expect(el.hasAttribute('disabled')).toBe(true);
        expect(el.getAttribute('aria-checked')).toBe('false');
      }
      const soft = radio('soft');
      expect(soft.hasAttribute('disabled')).toBe(false);
      // soft stays the default (always enabled in both modes).
      expect(soft.getAttribute('aria-checked')).toBe('true');
      const reboot = radio('reboot');
      expect(reboot.hasAttribute('disabled')).toBe(false);
    });

    it('shows the no-remote-power-on warning', () => {
      renderNoBmc();
      const warn = screen.getByText(
        /Without a BMC there is no way to power the server back on remotely/);
      expect(warn.textContent).toContain('Without a BMC');
    });

    it('a disabled IPMI verb cannot be selected or confirmed', () => {
      const { onConfirm } = renderNoBmc();
      fireEvent.click(radio('off'));
      // selection did not move off soft
      expect(radio('soft').getAttribute('aria-checked')).toBe('true');
      // confirm still fires the (enabled) soft, never the disabled off
      fireEvent.click(screen.getByRole('button', { name: 'soft' }));
      expect(onConfirm).toHaveBeenCalledWith('soft');
    });

    it('selecting reboot and confirming passes it to onConfirm', () => {
      const { onConfirm } = renderNoBmc();
      fireEvent.click(radio('reboot'));
      fireEvent.click(screen.getByRole('button', { name: 'reboot' }));
      expect(onConfirm).toHaveBeenCalledWith('reboot');
    });

    it('with a BMC the reboot row is absent', () => {
      renderDialog();
      expect(screen.queryByRole('radio', { name: /^reboot\b/ })).toBeNull();
    });
  });
});
