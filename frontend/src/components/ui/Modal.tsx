import { type ReactNode, useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { useI18n } from '../../i18n/context';

interface ModalProps {
  title: string;
  icon?: ReactNode;
  maxWidth?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  /** Optional action rendered on the title row, left of the close button. */
  headerAction?: ReactNode;
  /** Hide the scroll indicator (the panel stays scrollable). */
  hideScrollbar?: boolean;
  /**
   * Set to false for a modal opened on top of another modal (the deploy
   * modal from the server config modal): the overlay still catches outside
   * clicks, but the dark backdrop/blur is skipped so the parent modal's
   * backdrop is the only dimming layer.
   */
  backdrop?: boolean;
}

const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

// Modals can stack (e.g. a confirm dialog on top of the deploy modal); only
// the top-most one reacts to Escape. Audit 2026-10-22 FE-1: the old stack
// was dead code (never pushed, id always 0). Top-most is now derived from
// DOM order: all overlays share the same z-index, so whichever overlay comes
// later in the document — or is nested inside one that does — renders on
// top, and Escape must reach only that one. DOM order (not mount-effect
// order) is what decides this, and mount effects run child-first, so a
// naive push/pop stack would get nested modals backwards.
const mountedModals = new Set<HTMLElement>();

export function Modal({ title, icon, maxWidth = 'max-w-xl', onClose, children, footer, headerAction, hideScrollbar, backdrop = true }: ModalProps) {
  const { t } = useI18n();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    mountedModals.add(overlay);
    return () => { mountedModals.delete(overlay); };
  }, []);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLElement>('[data-modal-close]')?.focus();
    return () => { previouslyFocused?.focus(); };
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        // Another overlay later in document order (or nested after this
        // one) renders on top — Escape belongs to it, not to this modal.
        const mine = overlayRef.current;
        const covered = mine !== null && [...mountedModals].some(
          (other) => other !== mine
            && (mine.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
        );
        if (covered) return;
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
      ref={overlayRef}
      // No outside-click close (user ruling 2026-11-03: clicking outside
      // must not close any panel); the overlay still blocks the modal
      // underneath so its inputs are not clickable while this one is open.
      className={`fixed inset-0 z-[95] flex items-center justify-center p-4${backdrop ? ' bg-black/60 backdrop-blur-sm' : ''}`}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`bg-bg-card rounded-xl border border-border shadow-lg w-full ${maxWidth} max-h-[90vh] overflow-y-auto p-6${hideScrollbar ? ' no-scrollbar' : ''}`}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium flex items-center gap-2 text-base">{icon}{title}</h3>
          <div className="flex items-center gap-2">
            {headerAction}
            <button
              data-modal-close
              onClick={onClose}
              aria-label={t('Close')}
              className="p-1 rounded-lg hover:bg-bg-hover transition-colors text-text-muted"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
        {children}
        {footer && <div className="flex justify-end mt-4 gap-2">{footer}</div>}
      </div>
    </div>
  );
}
