import { useCallback, useEffect, useRef, useState } from 'react';
import { CHAT_MAX_TOKENS, openChatStream } from '../api/chat';
import type { ChatMessage, ChatUsage } from '../api/chat';

export interface ChatEntry {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  // Thinking text (delta.reasoning_content) — only populated when vLLM is
  // launched with --reasoning-parser.
  reasoning: string;
  streaming: boolean;
  error: string | null;
  usage: ChatUsage | null;
  elapsedMs: number | null;
}

// Keep the context window small: only the most recent turns are re-sent.
const MAX_HISTORY_MESSAGES = 20;

export function useChat() {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [streaming, setStreaming] = useState(false);
  // Messages typed while a generation is in flight; sent FIFO as each
  // generation finishes.
  const [queue, setQueue] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const idRef = useRef(0);
  const streamingRef = useRef(false);
  const queueRef = useRef<string[]>([]);
  // Mirrors the entries state synchronously so the queued-drain chain can
  // build the next message's history before React has re-rendered.
  const entriesRef = useRef<ChatEntry[]>([]);
  const mountedRef = useRef(true);

  // Abort an in-flight generation (and the queued drain) on unmount.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const updateEntries = useCallback((fn: (prev: ChatEntry[]) => ChatEntry[]) => {
    const next = fn(entriesRef.current);
    entriesRef.current = next;
    setEntries(next);
  }, []);

  const patchEntry = useCallback((id: number, fn: (e: ChatEntry) => ChatEntry) => {
    updateEntries((prev) => prev.map((e) => (e.id === id ? fn(e) : e)));
  }, [updateEntries]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    if (streamingRef.current) return;
    updateEntries(() => []);
  }, [updateEntries]);

  const removeQueued = useCallback((index: number) => {
    queueRef.current = queueRef.current.filter((_, i) => i !== index);
    setQueue(queueRef.current);
  }, []);

  const editQueued = useCallback((index: number, text: string) => {
    const trimmed = text.trim();
    if (!trimmed || index < 0 || index >= queueRef.current.length) return;
    const next = [...queueRef.current];
    next[index] = trimmed;
    queueRef.current = next;
    setQueue(queueRef.current);
  }, []);

  // One generation: stream the reply and append the deltas. Callers must
  // not run two at once — streamingRef is the gate.
  const runStream = useCallback(async (modelId: string, content: string) => {
    streamingRef.current = true;
    setStreaming(true);
    const history: ChatMessage[] = [
      ...entriesRef.current
        .filter((e) => !e.error && e.content.trim())
        .map((e) => ({ role: e.role, content: e.content })),
      { role: 'user' as const, content },
    ].slice(-MAX_HISTORY_MESSAGES);
    const userId = (idRef.current += 1);
    const asstId = (idRef.current += 1);
    updateEntries((prev) => [
      ...prev,
      { id: userId, role: 'user', content, reasoning: '', streaming: false, error: null, usage: null, elapsedMs: null },
      { id: asstId, role: 'assistant', content: '', reasoning: '', streaming: true, error: null, usage: null, elapsedMs: null },
    ]);
    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = performance.now();
    try {
      for await (const ev of openChatStream(modelId, history, CHAT_MAX_TOKENS, controller.signal)) {
        if (ev.delta) {
          patchEntry(asstId, (e) => ({ ...e, content: e.content + ev.delta }));
        }
        if (ev.reasoning) {
          patchEntry(asstId, (e) => ({ ...e, reasoning: e.reasoning + ev.reasoning }));
        }
        if (ev.usage) {
          patchEntry(asstId, (e) => ({ ...e, usage: ev.usage }));
        }
        if (ev.error) {
          patchEntry(asstId, (e) => ({ ...e, error: ev.error, streaming: false }));
        }
        if (ev.done) {
          patchEntry(asstId, (e) => ({ ...e, streaming: false, elapsedMs: performance.now() - startedAt }));
        }
      }
    } catch (e) {
      const aborted = (e as Error)?.name === 'AbortError';
      patchEntry(asstId, (en) => ({
        ...en,
        streaming: false,
        elapsedMs: performance.now() - startedAt,
        error: aborted ? en.error : (en.error ?? (e instanceof Error ? e.message : String(e))),
      }));
    } finally {
      streamingRef.current = false;
      abortRef.current = null;
      setStreaming(false);
    }
  }, [patchEntry, updateEntries]);

  const send = useCallback(async (modelId: string, text: string) => {
    const content = text.trim();
    if (!content) return;
    if (streamingRef.current) {
      // A generation is in flight: enqueue it; the drain loop below picks
      // it up when the current chain reaches the end.
      queueRef.current = [...queueRef.current, content];
      setQueue(queueRef.current);
      return;
    }
    // Drain the queue FIFO: each finished generation starts the next queued
    // message. The model is the one the chain began with — switching models
    // mid-chain surfaces as a chat error frame.
    let current = content;
    for (;;) {
      await runStream(modelId, current);
      if (!mountedRef.current) break;
      const next = queueRef.current[0];
      if (next === undefined) break;
      queueRef.current = queueRef.current.slice(1);
      setQueue(queueRef.current);
      current = next;
    }
  }, [runStream]);

  return { entries, streaming, send, stop, clear, queue, removeQueued, editQueued };
}
