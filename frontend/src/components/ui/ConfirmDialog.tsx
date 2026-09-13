import { AlertTriangle } from 'lucide-react';
import { Modal } from './Modal';
import { Button } from './Button';
import { useI18n } from '../../i18n';

interface ConfirmDialogProps {
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title, description, confirmLabel, cancelLabel, danger = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const { t } = useI18n();
  return (
    <Modal title={title} icon={<AlertTriangle className="w-5 h-5" />} maxWidth="max-w-md" onClose={onCancel}>
      {description && <p className="text-sm text-text-muted whitespace-pre-line">{description}</p>}
      <div className="flex justify-end mt-4 gap-2">
        <Button onClick={onCancel}>{cancelLabel ?? t('Cancel')}</Button>
        <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}
