import { useCallback, useEffect, useRef } from 'react';
import { getWsUrl } from '../api/client';
import { useConsoleStore } from '../stores/console';
import { useModelStore } from '../stores/model';
import { terminalReset, terminalSize, terminalWrite } from '../utils/terminalSink';
import { useI18n } from '../i18n/context';

const STARTUP_MARKERS = ['application startup complete', 'uvicorn running on'];

export function useConsoleWebSocket() {
  const { t } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const consoleWsRef = useRef<WebSocket | null>(null);
  const consoleManualCloseRef = useRef(false);
  const consoleReconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const consoleRetryCountRef = useRef(0);
  const consoleConnectSeqRef = useRef(0);

  const scheduleConsoleReconnect = () => {
    if (consoleReconnectRef.current) clearTimeout(consoleReconnectRef.current);
    // No attempt cap (audit 2026-10-22 FE-2, restoring the "Always
    // auto-reconnect" user ruling): the old 10-retry budget (~181 s) left
    // the console permanently dead after a 3-minute reboot, with no UI
    // recovery path. Exponential backoff capped at 30 s, same as the
    // hardware WebSocket.
    const delay = Math.min(1000 * Math.pow(2, consoleRetryCountRef.current), 30000);
    consoleRetryCountRef.current++;
    consoleReconnectRef.current = setTimeout(() => {
      // Always auto-reconnect (audit 2026-10-20 P2-15, user ruling): the
      // console is a read-only server terminal — having no RUNNING/LOADING
      // model is not a reason to stay disconnected. The remote PTY is kept
      // by the reattach design, so reconnecting re-attaches the same shell.
      connectConsoleWs('console');
    }, delay);
  };

  const connectConsoleWs = async (mode: 'start' | 'console', command?: string) => {
    // B2: getWsUrl is async (mints a one-time ticket when a key is stored).
    // The seq guard makes a superseded attempt (double-click, reconnect
    // overlapping a manual connect) drop out after the ticket round-trip
    // instead of opening a stale second socket.
    const seq = ++consoleConnectSeqRef.current;
    consoleManualCloseRef.current = false;
    if (consoleReconnectRef.current) { clearTimeout(consoleReconnectRef.current); consoleReconnectRef.current = null; }
    // FE-8 (audit 2026-10-22): the start command travels over THIS socket —
    // if the first attempt fails to connect, the command never reached the
    // PTY and the backend rolls the model back to STOPPED within seconds.
    // Say so once (terminal); retried sockets only log to the terminal.
    let firstStartAttempt = mode === 'start';
    const reportStartDeliveryFailure = (): boolean => {
      if (mode !== 'start' || !firstStartAttempt) return false;
      firstStartAttempt = false;
      const msg = tRef.current('Console connection failed — the start command may not have been delivered');
      terminalWrite(`\r\n\x1b[31m✗ ${msg}\x1b[0m\r\n`);
      return true;
    };
    let url: string;
    try {
      url = mode === 'start' ? await getWsUrl('/ws/console?mode=start') : await getWsUrl('/ws/console');
    } catch {
      if (seq === consoleConnectSeqRef.current) {
        reportStartDeliveryFailure();
        scheduleConsoleReconnect();
      }
      return;
    }
    if (seq !== consoleConnectSeqRef.current) return;
    const ws = new WebSocket(url);
    consoleWsRef.current = ws;
    let startupDetected = false;

    ws.onopen = () => {
      if (seq !== consoleConnectSeqRef.current || ws !== consoleWsRef.current) return;
      useConsoleStore.getState().setConnected(true);
      consoleRetryCountRef.current = 0;
      terminalReset();
      // Re-send the terminal size on (re)connect (audit 2026-10-20 P2-14):
      // a new/reattached session starts at the PTY 200x50 default and the
      // sendSize() fired at term-open was a no-op while the socket was not
      // yet OPEN — the PTY would stay 200 cols until the next window resize.
      const size = terminalSize();
      if (size) {
        ws.send(JSON.stringify({ type: 'resize', cols: size.cols, rows: size.rows }));
      }
      if (mode === 'start' && command) {
        ws.send(JSON.stringify({ command }));
      }
    };
    ws.onmessage = (event) => {
      let msg: { type?: string; data?: string };
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      switch (msg.type) {
        case 'output': {
          terminalWrite(msg.data ?? '');
          const text = (msg.data ?? '').toLowerCase();
          if (text.includes(STARTUP_MARKERS[0]) || text.includes(STARTUP_MARKERS[1])) {
            startupDetected = true;
            const { syncModelStatus, fetchModels } = useModelStore.getState();
            syncModelStatus().then(() => fetchModels());
          }
          break;
        }
        case 'buffer_truncated':
          terminalReset();
          break;
        case 'error':
          terminalWrite(`\r\n\x1b[31m✗ ${msg.data ?? ''}\x1b[0m\r\n`);
          break;
        case 'connected':
          terminalWrite(`\x1b[90m${msg.data ?? ''}\x1b[0m\r\n`);
          break;
      }
    };
    ws.onclose = () => {
      if (seq !== consoleConnectSeqRef.current || ws !== consoleWsRef.current) return;
      useConsoleStore.getState().setConnected(false);
      const { syncModelStatus, fetchModels } = useModelStore.getState();
      if (!startupDetected) syncModelStatus().then(() => fetchModels());
      if (!consoleManualCloseRef.current) scheduleConsoleReconnect();
    };
    ws.onerror = () => {
      if (seq !== consoleConnectSeqRef.current || ws !== consoleWsRef.current) return;
      if (mode === 'start') {
        if (!reportStartDeliveryFailure()) {
          terminalWrite(`\r\n\x1b[31m${tRef.current('✗ Connection error')}\x1b[0m\r\n`);
        }
      }
      useConsoleStore.getState().setConnected(false);
    };
  };
  const connectConsoleWsRef = useRef(connectConsoleWs);
  connectConsoleWsRef.current = connectConsoleWs;

  const connectConsole = useCallback(() => {
    const cur = consoleWsRef.current;
    if (cur && (cur.readyState === WebSocket.OPEN || cur.readyState === WebSocket.CONNECTING)) return;
    connectConsoleWsRef.current('console');
  }, []);

  const startConsoleForModel = useCallback((command: string) => {
    consoleManualCloseRef.current = true;
    if (consoleWsRef.current) {
      consoleWsRef.current.close();
    }
    if (consoleReconnectRef.current) { clearTimeout(consoleReconnectRef.current); consoleReconnectRef.current = null; }
    connectConsoleWsRef.current('start', command);
  }, []);

  const sendConsoleInput = useCallback((data: string) => {
    const ws = consoleWsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data }));
    }
  }, []);

  const sendConsoleResize = useCallback((cols: number, rows: number) => {
    const ws = consoleWsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'resize', cols, rows }));
    }
  }, []);

  useEffect(() => {
    return () => {
      // Audit 2026-10-23 D-3: invalidate a connect attempt still awaiting
      // the ticket round-trip — without this, `new WebSocket` runs after
      // unmount and the orphaned socket leaks (nothing can close it).
      consoleConnectSeqRef.current++;
      consoleManualCloseRef.current = true;
      if (consoleWsRef.current) { consoleWsRef.current.close(); consoleWsRef.current = null; }
      if (consoleReconnectRef.current) { clearTimeout(consoleReconnectRef.current); consoleReconnectRef.current = null; }
      useConsoleStore.getState().setConnected(false);
    };
  }, []);

  return { connectConsole, startConsoleForModel, sendConsoleInput, sendConsoleResize };
}
