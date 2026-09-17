import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cancelTask,
  getTask,
  getTaskLog,
  getTasks,
  type DeployTask,
} from '../api/deployment';

const POLL_MS = 3000;
const LOG_POLL_MS = 2500;
const LOG_LINES = 300;

export function useTasks() {
  const [tasks, setTasks] = useState<DeployTask[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<DeployTask | null>(null);
  const [logText, setLogText] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const logOffsetRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const refresh = useCallback(async () => {
    try {
      const list = await getTasks(30);
      setTasks(list);
    } catch {
      // transient (SSH/API hiccup) — keep the previous list
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  // Selected task: detail + incremental log while it is active.
  useEffect(() => {
    if (!selectedId) {
      setSelectedTask(null);
      setLogText('');
      return;
    }
    let cancelled = false;
    logOffsetRef.current = 0;
    const load = async () => {
      try {
        const detail = await getTask(selectedId);
        if (cancelled) return;
        setSelectedTask(detail);
        const { content, offset } = await getTaskLog(selectedId, logOffsetRef.current);
        if (cancelled) return;
        logOffsetRef.current = offset;
        if (content) setLogText((prev) => {
          const next = prev + content;
          const lines = next.split('\n');
          return lines.length > LOG_LINES ? lines.slice(-LOG_LINES).join('\n') : next;
        });
      } catch {
        // task may have been evicted — drop the selection
        if (!cancelled) setSelectedId(null);
      }
    };
    load();
    const timer = setInterval(load, LOG_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selectedId]);

  const activeCount = tasks.filter((t) =>
    ['queued', 'running', 'awaiting_reboot'].includes(t.status)
  ).length;

  const cancelSelected = useCallback(async () => {
    if (!selectedIdRef.current) return;
    try {
      await cancelTask(selectedIdRef.current);
    } catch {
      // already finished / not cancellable
    }
  }, []);

  const cancelTaskById = useCallback(async (id: string) => {
    try {
      await cancelTask(id);
    } catch {
      // already finished / not cancellable
    }
  }, []);

  const downloadLog = useCallback(async () => {
    if (!selectedIdRef.current) return;
    try {
      const { content } = await getTaskLog(selectedIdRef.current, 0);
      const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${selectedIdRef.current}.log`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // ignore
    }
  }, []);

  return {
    tasks,
    selectedId,
    selectedTask,
    logText,
    drawerOpen,
    activeCount,
    openDrawer: () => setDrawerOpen(true),
    closeDrawer: () => {
      setDrawerOpen(false);
      setSelectedId(null);
    },
    selectTask: (id: string | null) => setSelectedId(id),
    cancelSelected,
    cancelTaskById,
    downloadLog,
    refresh,
  };
}

export type UseTasks = ReturnType<typeof useTasks>;
