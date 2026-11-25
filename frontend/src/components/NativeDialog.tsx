import React, { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';

interface NativeDialogProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

/**
 * Native <dialog> modal wrapper following modern-web-guidance `dialog` guidelines:
 * - No redundant role="dialog" or aria-modal
 * - Uses showModal() + close listener resetting returnValue
 * - Includes closedby="any" + pointerdown/pointerup outside-click fallback for Safari/WebKit
 */
export const NativeDialog: React.FC<NativeDialogProps> = ({ open, title, onClose, children }) => {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      dlg.showModal();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;

    const handleClose = () => {
      dlg.returnValue = '';
      onClose();
    };

    dlg.addEventListener('close', handleClose);

    // Fallback light-dismiss when HTMLDialogElement.prototype.closedBy is missing (Safari/macOS WebKit)
    let pressedOutside = false;
    const isOutside = (e: PointerEvent) => {
      const r = dlg.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    const onPointerDown = (e: PointerEvent) => {
      if (dlg.open) pressedOutside = isOutside(e);
    };
    const onPointerUp = (e: PointerEvent) => {
      if (dlg.open && pressedOutside && isOutside(e)) {
        dlg.close('dismiss');
      }
      pressedOutside = false;
    };

    const needsLightDismissPolyfill = !('closedBy' in HTMLDialogElement.prototype);
    if (needsLightDismissPolyfill) {
      dlg.addEventListener('pointerdown', onPointerDown);
      dlg.addEventListener('pointerup', onPointerUp);
    }

    return () => {
      dlg.removeEventListener('close', handleClose);
      if (needsLightDismissPolyfill) {
        dlg.removeEventListener('pointerdown', onPointerDown);
        dlg.removeEventListener('pointerup', onPointerUp);
      }
    };
  }, [onClose]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      {...({ closedby: 'any' } as Record<string, string>)}
    >
      <div className="p-5">
        <div className="flex items-center justify-between gap-3 pb-3 border-b border-slate-200">
          <h2 id={titleId} className="text-base font-bold text-[#0F2942]">
            {title}
          </h2>
          <button
            type="button"
            onClick={() => ref.current?.close('cancel')}
            className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            aria-label="창 닫기"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="pt-4">{children}</div>
      </div>
    </dialog>
  );
};
