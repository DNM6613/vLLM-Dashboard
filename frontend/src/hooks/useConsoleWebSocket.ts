import { useCallback, useEffect, useRef } from 'react';
import { getWsUrl } from '../api/client';
import { useConsoleStore } from '../stores/console';
import { useModelStore } from '../stores/model';
import { terminalReset, terminalWrite } from '../utils/terminalSink';
import { ModelStatus } from '../types';
import { useI18n } from '../i18n';

const STARTUP_MARKERS = ['application startup complete', 'uvicorn running on'];

export function useConsoleWebSocket() {
  const { t } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const consoleWsRef = useRef<WebSocket | null>(null);
  const consoleManualCloseRef = useRef(false);
  const consoleReconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const consoleRetryCountRef = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 10;

  const scheduleConsoleReconnect = () => {
    if (consoleReconnectRef.current) clearTimeout(consoleReconnectRef.current);
    if (consoleRetryCountRef.current >= MAX_RECONNECT_ATTEMPTS) {
      terminalWrite(`\r\n\x1b[31m${tRef.current('[WebSocket] Reconnect limit reached. Check the AI server and network.')}\x1b[0m\r\n`);
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, consoleRetryCountRef.current), 30000);
    consoleRetryCountRef.current++;
    consoleReconnectRef.current = setTimeout(() => {
      const currentModels = useModelStore.getState().models;
      if (!currentModels.some(m => m.status === ModelStatus.RUNNING || m.status === ModelStatus.LOADING)) return;
      connectConsoleWs('console');
    }, delay);
  };

  const connectConsoleWs = (mode: 'start' | 'console', command?: string) => {
    consoleManualCloseRef.current = false;
    if (consoleReconnectRef.current) { clearTimeout(consoleReconnectRef.current); consoleReconnectRef.current = null; }
    const url = mode === 'start' ? getWsUrl('/ws/console?mode=start') : getWsUrl('/ws/console');
    const ws = new WebSocket(url);
    consoleWsRef.current = ws;
    let startupDetected = false;

    ws.onopen = () => {
      if (ws !== consoleWsRef.current) return;
      useConsoleStore.getState().setConnected(true);
      consoleRetryCountRef.current = 0;
      terminalReset();
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
      if (ws !== consoleWsRef.current) return;
      useConsoleStore.getState().setConnected(false);
      const { syncModelStatus, fetchModels } = useModelStore.getState();
      if (!startupDetected) syncModelStatus().then(() => fetchModels());
      if (!consoleManualCloseRef.current) scheduleConsoleReconnect();
    };
    ws.onerror = () => {
      if (ws !== consoleWsRef.current) return;
      if (mode === 'start') {
        terminalWrite(`\r\n\x1b[31m${tRef.current('✗ Connection error')}\x1b[0m\r\n`);
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
      consoleManualCloseRef.current = true;
      if (consoleWsRef.current) { consoleWsRef.current.close(); consoleWsRef.current = null; }
      if (consoleReconnectRef.current) { clearTimeout(consoleReconnectRef.current); consoleReconnectRef.current = null; }
      useConsoleStore.getState().setConnected(false);
    };
  }, []);

  return { connectConsole, startConsoleForModel, sendConsoleInput, sendConsoleResize };
}
