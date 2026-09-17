import { useEffect, useRef } from 'react';
import { FileTerminal, Loader2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

interface DownloadLogModalProps {
  modelRepo: string;
  log: string;
  downloading: boolean;
  onClose: () => void;
}

export function DownloadLogModal({ modelRepo, log, downloading, onClose }: DownloadLogModalProps) {
  const { t } = useI18n();
  const scrollRef = useRef<HTMLPreElement | null>(null);
  const stickToBottomRef = useRef(true);

  // Follow the newest output while the download is running, unless the user
  // has scrolled up to read earlier lines.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (downloading && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [log, downloading]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  return (
    <Modal
      title={t('Download log')}
      icon={downloading ? <Loader2 className="w-5 h-5 animate-spin" /> : <FileTerminal className="w-5 h-5" />}
      maxWidth="max-w-3xl"
      onClose={onClose}
      footer={<Button onClick={onClose}>{t('Close')}</Button>}
    >
      <div className="text-xs text-text-muted mb-2 font-mono truncate" title={modelRepo}>{modelRepo}</div>
      <pre
        ref={scrollRef}
        onScroll={onScroll}
        className="bg-bg rounded-lg border border-border p-3 max-h-[60vh] overflow-y-auto text-xs font-mono whitespace-pre-wrap break-words text-text leading-relaxed"
      >
        {log || <span className="text-text-muted">{t('No log output yet')}</span>}
      </pre>
    </Modal>
  );
}
