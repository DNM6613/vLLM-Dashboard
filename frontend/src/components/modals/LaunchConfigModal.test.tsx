import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { I18nProvider } from '../../i18n';
import { LaunchConfigModal } from './LaunchConfigModal';

function mockScrollHeight(ctl: { value: number }) {
  Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollHeight', {
    configurable: true,
    get: () => ctl.value,
  });
}

function renderModal(envVars = 'A=1', commandValue = 'vllm serve /m') {
  function Harness() {
    const [env, setEnv] = useState(envVars);
    const [cmd, setCmd] = useState(commandValue);
    return (
      <I18nProvider>
        <LaunchConfigModal
          modelName="m"
          modelPath="/models/m"
          command={cmd}
          onCommandChange={setCmd}
          envVars={env}
          onEnvVarsChange={setEnv}
          onSave={() => {}}
          saving={false}
          onClose={() => {}}
        />
      </I18nProvider>
    );
  }
  const utils = render(<Harness />);
  const env = utils.container.querySelector('#lc-env-vars') as HTMLTextAreaElement;
  const cmd = utils.container.querySelector('#lc-start-command') as HTMLTextAreaElement;
  return { ...utils, env, cmd };
}

describe('LaunchConfigModal textarea auto-resize', () => {
  const ctl = { value: 100 };

  beforeEach(() => {
    ctl.value = 100;
    mockScrollHeight(ctl);
  });

  afterEach(() => {
    delete (HTMLTextAreaElement.prototype as unknown as { scrollHeight?: unknown }).scrollHeight;
    vi.restoreAllMocks();
  });

  it('sizes each textarea to content (scrollHeight) + one line (lineHeight)', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ lineHeight: '20px' } as unknown as CSSStyleDeclaration);
    const { env, cmd } = renderModal();
    expect(env.style.height).toBe('120px');
    expect(cmd.style.height).toBe('120px');
  });

  it('falls back to a 20px line when computed lineHeight is not numeric', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ lineHeight: 'normal' } as unknown as CSSStyleDeclaration);
    ctl.value = 60;
    const { env } = renderModal();
    expect(env.style.height).toBe('80px');
  });

  it('hides scrollbars on both textareas', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ lineHeight: '20px' } as unknown as CSSStyleDeclaration);
    const { env, cmd } = renderModal();
    expect(env.className).toContain('no-scrollbar');
    expect(cmd.className).toContain('no-scrollbar');
  });

  it('grows when the content grows', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ lineHeight: '20px' } as unknown as CSSStyleDeclaration);
    const { env } = renderModal();
    expect(env.style.height).toBe('120px');
    ctl.value = 200;
    fireEvent.change(env, { target: { value: 'A=1\nB=2' } });
    expect(env.style.height).toBe('220px');
  });
});
