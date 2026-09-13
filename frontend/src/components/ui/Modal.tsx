import { type ReactNode, useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { useI18n } from '../../i18n';

interface ModalProps {
  title: string;
  icon?: ReactNode;
  maxWidth?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}

const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function Modal({ title, icon, maxWidth = 'max-w-xl', onClose, children, footer }: ModalProps) {
  const { t } = useI18n();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLElement>('[data-modal-close]')?.focus();
    return () => { previouslyFocused?.focus(); };
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
        .filter((el) => !el.hasAttribute('disabled'));
      if (focusables.length === 0) { e.preventDefault(); return; }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, []);

  return (
    <div
      className="fixed inset-0 z-[95] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCloseRef.current(); }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`bg-bg-card rounded-xl border border-border shadow-lg w-full ${maxWidth} max-h-[90vh] overflow-y-auto p-6`}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium flex items-center gap-2 text-base">{icon}{title}</h3>
          <button
            data-modal-close
            onClick={onClose}
            aria-label={t('Close')}
            className="p-1 rounded-lg hover:bg-bg-hover transition-colors text-text-muted"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
        {footer && <div className="flex justify-end mt-4 gap-2">{footer}</div>}
      </div>
    </div>
  );
}
