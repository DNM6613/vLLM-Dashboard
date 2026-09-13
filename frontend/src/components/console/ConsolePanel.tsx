import { memo, useEffect, useLayoutEffect, useRef } from 'react';
import { Terminal as TerminalIcon } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { registerTerminalSink } from '../../utils/terminalSink';
import { decideCtrlC } from '../../utils/ctrlC';
import { useI18n } from '../../i18n';
import { getCurrentTheme, useTheme } from '../../hooks/useTheme';

interface ConsolePanelProps {
  onSendInput: (data: string) => void;
  onSendResize: (cols: number, rows: number) => void;
}

const XTERM_THEME_DARK = {
  background: '#141c28',
  foreground: '#e6edf3',
  cursor: '#e6edf3',
  cursorAccent: '#141c28',
  selectionBackground: '#264f78',
  black: '#484f58',
  red: '#ff7b72',
  green: '#3fb950',
  yellow: '#d29922',
  blue: '#58a6ff',
  magenta: '#bc8cff',
  cyan: '#39c5cf',
  white: '#b1bac4',
  brightBlack: '#6e7681',
  brightRed: '#ffa198',
  brightGreen: '#56d364',
  brightYellow: '#e3b341',
  brightBlue: '#79c0ff',
  brightMagenta: '#d2a8ff',
  brightCyan: '#56d4dd',
  brightWhite: '#f0f6fc',
};

const XTERM_THEME_LIGHT = {
  background: '#faf6ec',
  foreground: '#3f3a2c',
  cursor: '#3f3a2c',
  cursorAccent: '#faf6ec',
  selectionBackground: '#e6dcbe',
  black: '#57534a',
  red: '#cf222e',
  green: '#1a7f37',
  yellow: '#9a6700',
  blue: '#0550ae',
  magenta: '#8250df',
  cyan: '#1b7c83',
  white: '#57534a',
  brightBlack: '#6e6a5c',
  brightRed: '#cf222e',
  brightGreen: '#1a7f37',
  brightYellow: '#9a6700',
  brightBlue: '#0550ae',
  brightMagenta: '#8250df',
  brightCyan: '#1b7c83',
  brightWhite: '#3f3a2c',
};

export const ConsolePanel = memo(function ConsolePanel({ onSendInput, onSendResize }: ConsolePanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const { t } = useI18n();
  const { theme } = useTheme();
  const lastCtrlCRef = useRef(0);

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
    const ro = new ResizeObserver(() => {
      fit.fit();
      sendSize();
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      registerTerminalSink(null);
      termRef.current = null;
      term.dispose();
    };
  }, [onSendInput, onSendResize]);

  useLayoutEffect(() => {
    const term = termRef.current;
    if (term) {
      term.options.theme = theme === 'light' ? XTERM_THEME_LIGHT : XTERM_THEME_DARK;
    }
  }, [theme]);

  return (
    <div className="bg-bg-card rounded-xl border border-border overflow-hidden lg:sticky lg:top-4 flex flex-col shadow-md lg:h-[calc(100vh-104px)]">
      <div className="flex items-center p-3 border-b border-border">
        <div className="flex items-center gap-2">
          <TerminalIcon className="w-4 h-4" />
          <span className="text-sm font-medium">{t('Console')}</span>
        </div>
      </div>
      <div className="flex-1 min-h-0 h-[50vh] lg:h-auto p-4 bg-[rgb(var(--c-console))]">
        <div ref={containerRef} className="h-full w-full flex items-center" />
      </div>
    </div>
  );
});

ConsolePanel.displayName = 'ConsolePanel';
