import { useEffect } from 'react';
import { getWsUrl } from '../api/client';
import { useHardwareStore } from '../stores/hardware';
import { useModelStatusStore } from '../stores/modelStatus';

export function useHardwareWebSocket() {
  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let isManualClose = false;
    let retryCount = 0;
    let lastFrameAt = 0;
    const stallTimer = setInterval(() => {
      if (lastFrameAt && Date.now() - lastFrameAt > 15000) {
        const s = useModelStatusStore.getState();
        if (s.model !== null) s.clearModel();
      }
    }, 5000);

    const connect = () => {
      const socket = new WebSocket(getWsUrl('/ws/hardware'));
      ws = socket;
      socket.onopen = () => {
        retryCount = 0;
      };
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'gpu_metrics') {
            lastFrameAt = Date.now();
            useHardwareStore.getState().addMetrics(data.data);
            useModelStatusStore.getState().setModel(data.data.model ?? null);
          }
        } catch {  }
      };
      socket.onclose = () => {
        if (!isManualClose) {
          const delay = Math.min(1000 * Math.pow(2, retryCount), 30000);
          retryCount++;
          reconnectTimer = setTimeout(connect, delay);
        }
      };
    };

    connect();

    return () => {
      isManualClose = true;
      clearInterval(stallTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);
}
