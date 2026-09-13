export interface ToastItem {
  id: number;
  message: string;
  type: 'error' | 'info';
}

type Listener = (toasts: readonly ToastItem[]) => void;

const AUTO_DISMISS_MS = 8000;

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l(items);
}

export function showToast(message: string, type: 'error' | 'info' = 'error') {
  const id = nextId++;
  items = [...items, { id, message, type }];
  emit();
  setTimeout(() => dismissToast(id), AUTO_DISMISS_MS);
}

export function dismissToast(id: number) {
  if (!items.some((t) => t.id === id)) return;
  items = items.filter((t) => t.id !== id);
  emit();
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  listener(items);
  return () => { listeners.delete(listener); };
}
