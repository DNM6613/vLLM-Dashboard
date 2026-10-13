import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import type { ServerConfig } from '../../types';
import { ServerConfigModal } from './ServerConfigModal';

const baseConfig: ServerConfig = {
  id: 'default', host: '10.0.0.1', use_auth: false, ssh_port: 22,
  ssh_username: 'uforce', venv_name: '.vllm', bmc_host: '', bmc_username: '',
};

type SavedPayload = Partial<ServerConfig> | undefined;

function Harness({ initial, onSave }: {
  initial: ServerConfig;
  onSave: (p?: Partial<ServerConfig>) => void;
}) {
  const [config, setConfig] = useState(initial);
  return (
    <I18nProvider>
      <ServerConfigModal
        config={config}
        onConfigChange={setConfig}
        onSave={onSave}
        saving={false}
        message={null}
        onClose={() => {}}
        onOpenDeployEnv={() => {}}
      />
    </I18nProvider>
  );
}

function hfTokenInput(): HTMLInputElement {
  const input = document.querySelector('#srv-hf-token');
  if (!(input instanceof HTMLInputElement)) throw new Error('#srv-hf-token input not found');
  return input;
}

function findButton(re: RegExp): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll('button'))
    .find((b) => re.test(b.textContent ?? ''));
  if (!btn) throw new Error(`button matching ${re} not found`);
  return btn as HTMLButtonElement;
}

describe('ServerConfigModal HF token', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders the HF token field as a password input', () => {
    render(<Harness initial={baseConfig} onSave={() => {}} />);
    const input = hfTokenInput();
    expect(input.type).toBe('password');
    expect(input.value).toBe('');
  });

  it('submits the entered HF token on save', () => {
    const saved: SavedPayload[] = [];
    render(<Harness initial={baseConfig} onSave={(p) => { saved.push(p); }} />);
    fireEvent.change(hfTokenInput(), { target: { value: 'hf_abc123' } });
    fireEvent.click(findButton(/Save|保存/));
    expect(saved.length).toBe(1);
    expect(saved[0]?.hf_token).toBe('hf_abc123');
  });

  it('round-trips the mask for a stored token when left untouched', () => {
    const saved: SavedPayload[] = [];
    render(<Harness initial={{ ...baseConfig, hf_token: '***' }} onSave={(p) => { saved.push(p); }} />);
    expect(hfTokenInput().value).toBe('***');
    fireEvent.click(findButton(/Save|保存/));
    expect(saved.length).toBe(1);
    expect(saved[0]?.hf_token).toBe('***');
  });

  it('asks for confirmation before erasing a stored token, and clears on confirm', () => {
    const saved: SavedPayload[] = [];
    render(<Harness initial={{ ...baseConfig, hf_token: '***' }} onSave={(p) => { saved.push(p); }} />);
    fireEvent.change(hfTokenInput(), { target: { value: '' } });
    fireEvent.click(findButton(/Save|保存/));
    // First click only opens the confirm dialog — nothing is saved yet.
    expect(saved.length).toBe(0);
    fireEvent.click(findButton(/Confirm|确认/));
    expect(saved.length).toBe(1);
    expect(saved[0]?.hf_token).toBe('');
  });
});
