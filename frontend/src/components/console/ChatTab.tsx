import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Pencil, Send, Square, X } from 'lucide-react';
import { useI18n } from '../../i18n/context';
import { useChat } from '../../hooks/useChat';
import { getLaunchConfig } from '../../api/models';
import { Button } from '../ui/Button';
import { ModelStatus } from '../../types';
import type { ModelInfo } from '../../types';

const SHOW_THINKING_KEY = 'vllm_chat_show_thinking';

function readShowThinking(): boolean {
  try {
    return localStorage.getItem(SHOW_THINKING_KEY) !== '0';
  } catch {
    return true;
  }
}

function writeShowThinking(value: boolean): void {
  try {
    localStorage.setItem(SHOW_THINKING_KEY, value ? '1' : '0');
  } catch {
    // non-persistent storage (private mode) — the toggle still works this session
  }
}

interface ChatTabProps {
  models: ModelInfo[];
}

export function ChatTab({ models }: ChatTabProps) {
  const { t } = useI18n();
  // Same mechanism as the benchmark modal: chat is bound to the single
  // currently running model — there is no selector.
  const runningModel = useMemo(() => models.find((m) => m.status === ModelStatus.RUNNING), [models]);
  const modelId = runningModel?.id ?? '';
  const [input, setInput] = useState('');
  const [thinkingSupported, setThinkingSupported] = useState(false);
  const [showThinking, setShowThinking] = useState(readShowThinking);
  const { entries, streaming, send, stop, queue, removeQueued, editQueued } = useChat();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const wasStreaming = useRef(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState('');

  const startEdit = (i: number) => {
    setEditing(i);
    setDraft(queue[i]);
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft('');
  };

  // Empty draft cancels; anything else replaces the queued item in place.
  const commitEdit = (i: number) => {
    const text = draft.trim();
    if (text) editQueued(i, text);
    cancelEdit();
  };

  // The thinking toggle is only offered when this model's launch command
  // carries --reasoning-parser (without it, thinking is baked into the
  // regular content and cannot be separated).
  useEffect(() => {
    if (!modelId) {
      setThinkingSupported(false);
      return;
    }
    let alive = true;
    getLaunchConfig(modelId)
      .then((r) => {
        if (alive) setThinkingSupported(r.config?.start_command.includes('--reasoning-parser') ?? false);
      })
      .catch(() => {
        if (alive) setThinkingSupported(false);
      });
    return () => {
      alive = false;
    };
  }, [modelId]);

  // Hand focus back when a generation (or the queued drain) finishes so the
  // user can keep typing without re-clicking.
  useEffect(() => {
    if (wasStreaming.current && !streaming) inputRef.current?.focus();
    wasStreaming.current = streaming;
  }, [streaming]);

  // Keep the transcript pinned to the latest line.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  // While a generation is in flight this queues the message (the hook's
  // streaming gate decides send-now vs enqueue); Enter never drops input.
  const handleSend = () => {
    const text = input.trim();
    if (!text || !modelId) return;
    setInput('');
    void send(modelId, text);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {entries.length === 0 ? (
          !runningModel && (
            <div className="py-4 text-center text-xs text-text-muted">{t('No running model')}</div>
          )
        ) : (
          <div className="space-y-3">
            {entries.map((e) => {
              const usage = e.usage;
              const ms = e.elapsedMs;
              const tps = usage?.completion_tokens && ms && ms > 0
                ? (usage.completion_tokens / (ms / 1000)).toFixed(1)
                : null;
              return (
                <div key={e.id}>
                  <div className="mb-0.5 text-[10px] text-text-muted">{e.role === 'user' ? t('You') : t('Model')}</div>
                  {showThinking && e.reasoning && (
                    <details open className="mb-1 rounded-lg border border-border bg-bg-hover/40 px-2 py-1">
                      <summary className="cursor-pointer text-[10px] text-text-muted select-none">{t('Thinking')}</summary>
                      <pre className="mt-1 max-h-40 overflow-y-auto font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all text-text-muted">{e.reasoning}</pre>
                    </details>
                  )}
                  {e.content ? (
                    <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">{e.content}</pre>
                  ) : e.streaming ? (
                    <Loader2 className="w-3.5 h-3.5 text-accent animate-spin" />
                  ) : null}
                  {e.error && <div className="mt-1 font-mono text-xs text-danger break-all">{e.error}</div>}
                  {usage && ms != null && (
                    <div className="mt-1 text-[10px] text-text-muted">
                      {t('Chat stats: {tokens} tokens · {secs}s · {tps} tok/s', {
                        tokens: usage.completion_tokens ?? 0,
                        secs: (ms / 1000).toFixed(1),
                        tps: tps ?? '—',
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {queue.length > 0 && (
        <div className="shrink-0 space-y-1">
          <div className="text-[10px] text-text-muted">{t('Queued ({n})', { n: queue.length })}</div>
          {queue.map((q, i) => (
            <div key={`${i}:${q}`} className="flex items-center gap-1.5 rounded-lg border border-border bg-bg px-2 py-1">
              {editing === i ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      commitEdit(i);
                    } else if (e.key === 'Escape') {
                      e.preventDefault();
                      cancelEdit();
                    }
                  }}
                  onBlur={() => commitEdit(i)}
                  className="min-w-0 flex-1 bg-transparent font-mono text-xs outline-none"
                />
              ) : (
                <span className="min-w-0 flex-1 truncate font-mono text-xs" title={q}>{q}</span>
              )}
              {editing !== i && (
                <button
                  type="button"
                  title={t('Edit queued message')}
                  onClick={() => startEdit(i)}
                  className="shrink-0 rounded p-0.5 text-text-muted hover:bg-bg-hover hover:text-accent"
                >
                  <Pencil className="h-3 w-3" />
                </button>
              )}
              <button
                type="button"
                title={t('Remove from queue')}
                onClick={() => {
                  if (editing === i) cancelEdit();
                  removeQueued(i);
                }}
                className="shrink-0 rounded p-0.5 text-text-muted hover:bg-bg-hover hover:text-danger"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex shrink-0 items-end gap-2">
        <textarea
          ref={inputRef}
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder={t('Chat input')}
          disabled={!modelId}
          className="flex-1 resize-none rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs disabled:opacity-50"
        />
        <div className="flex flex-col items-end gap-1.5">
          {thinkingSupported && (
            <label className="flex cursor-pointer items-center gap-1 text-xs text-text-muted select-none">
              <input
                type="checkbox"
                checked={showThinking}
                onChange={(e) => {
                  setShowThinking(e.target.checked);
                  writeShowThinking(e.target.checked);
                }}
                className="accent-sky-400"
              />
              {t('Show thinking')}
            </label>
          )}
          {streaming ? (
            <Button size="sm" onClick={stop}>
              <Square className="w-3 h-3" /> {t('Stop')}
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={handleSend} disabled={!modelId || !input.trim()}>
              <Send className="w-3 h-3" /> {t('Send')}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
