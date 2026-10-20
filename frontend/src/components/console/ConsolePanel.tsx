import { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { MessageCircle, Terminal as TerminalIcon } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { registerTerminalSink } from '../../utils/terminalSink';
import { decideCtrlC } from '../../utils/ctrlC';
import { useI18n } from '../../i18n/context';
import { getCurrentTheme, useTheme } from '../../hooks/useTheme';
import { XTERM_THEME_DARK, XTERM_THEME_LIGHT } from '../../styles/terminalTheme';
import { ChatTab } from './ChatTab';
import { ConnectionsPanel } from './ConnectionsPanel';
import type { ModelInfo } from '../../types';

type ConsoleTab = 'console' | 'chat';

interface ConsolePanelProps {
  models: ModelInfo[];
  onSendInput: (data: string) => void;
  onSendResize: (cols: number, rows: number) => void;
}

function TabButton({ active, onClick, label, icon }: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs transition-colors ${
        active ? 'bg-bg-hover text-text' : 'text-text-muted hover:bg-bg-hover/50 hover:text-text'
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

export const ConsolePanel = memo(function ConsolePanel({ models, onSendInput, onSendResize }: ConsolePanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<(() => void) | null>(null);
  const { t } = useI18n();
  const { theme } = useTheme();
  const lastCtrlCRef = useRef(0);
  const [tab, setTab] = useState<ConsoleTab>('console');

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 12,
      fontFamily: "ui-monospace, SFMono-Regular, 'Cascadia Mono', Menlo, Consolas, monospace",
      scrollback: 5000,
      theme: getCurrentTheme() === 'light' ? XTERM_THEME_LIGHT : XTERM_THEME_DARK,
    });
    termRef.current = term;
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true;
      const isCtrlC =
        (ev.ctrlKey || ev.metaKey) && !ev.altKey && !ev.shiftKey && ev.code === 'KeyC';
      if (!isCtrlC) return true;
      if (ev.repeat) return false;
      const now = Date.now();
      if (decideCtrlC(now, lastCtrlCRef.current) === 'sigint') {
        lastCtrlCRef.current = 0;
        ev.preventDefault();
        onSendInput('\x03');
      } else {
        lastCtrlCRef.current = now;
      }
      return false;
    });
    term.open(container);
    const core = term as unknown as { _core?: { viewport?: { scrollBarWidth: number } } };
    if (core._core?.viewport) core._core.viewport.scrollBarWidth = 0;
    fit.fit();
    registerTerminalSink({ write: (d) => term.write(d), reset: () => term.reset() });
    term.onData((d) => onSendInput(d));
    const sendSize = () => onSendResize(term.cols, term.rows);
    sendSize();
    fitRef.current = () => {
      fit.fit();
      sendSize();
    };
    // While the Chat tab is active the container is display:none and the
    // observer reports 0x0 — skip fit() in that case or xterm gets cols 0.
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect || rect.width === 0 || rect.height === 0) return;
      fit.fit();
      sendSize();
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      fitRef.current = null;
      registerTerminalSink(null);
      termRef.current = null;
      term.dispose();
    };
  }, [onSendInput, onSendResize]);

  // Coming back from the Chat tab, re-fit to restore cols/rows.
  useEffect(() => {
    if (tab === 'console') fitRef.current?.();
  }, [tab]);

  useLayoutEffect(() => {
    const term = termRef.current;
    if (term) {
      term.options.theme = theme === 'light' ? XTERM_THEME_LIGHT : XTERM_THEME_DARK;
    }
  }, [theme]);

  return (
    <div className="bg-bg-card rounded-xl border border-border overflow-hidden lg:sticky lg:top-4 flex flex-col shadow-md lg:h-[calc(100vh-104px)]">
      <div className="flex items-center gap-1 p-1.5 pr-3 border-b border-border">
        <TabButton active={tab === 'console'} onClick={() => setTab('console')} label={t('Console')} icon={<TerminalIcon className="w-4 h-4" />} />
        <TabButton active={tab === 'chat'} onClick={() => setTab('chat')} label={t('Chat')} icon={<MessageCircle className="w-4 h-4" />} />
      </div>
      {/* 3/4 content (console + chat) / 1/4 access log (接入情况) */}
      <div className="flex-1 min-h-0 h-[64vh] lg:h-auto flex flex-col">
        <div className="flex-[3] min-h-0 p-4 bg-[rgb(var(--c-console))]">
          <div className="h-full" style={tab === 'console' ? undefined : { display: 'none' }}>
            <div ref={containerRef} className="h-full w-full flex items-center" />
          </div>
          <div className="h-full" style={tab === 'chat' ? undefined : { display: 'none' }}>
            <ChatTab models={models} />
          </div>
        </div>
        <div className="flex-1 min-h-0 border-t border-border">
          <ConnectionsPanel />
        </div>
      </div>
    </div>
  );
});

ConsolePanel.displayName = 'ConsolePanel';
