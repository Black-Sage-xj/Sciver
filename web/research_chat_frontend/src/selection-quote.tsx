import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Quote } from 'lucide-react';

type SelectedQuote = { text: string; right: number; top: number; bottom: number };

export function SelectionQuotePopover({ onQuote }: { onQuote: (text: string) => void }) {
  const [selection, setSelection] = useState<SelectedQuote | null>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = document.getElementById('tm-research-chat-app');
    let selecting = false;
    let frame = 0;
    const dismiss = () => { cancelAnimationFrame(frame); setSelection(null); };
    const update = () => {
      if (selecting) return;
      const selected = window.getSelection();
      const text = selected?.toString().trim();
      if (!selected || selected.isCollapsed || !selected.rangeCount || !text) { dismiss(); return; }
      const range = selected.getRangeAt(0);
      const element = range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement;
      const content = element?.closest('.tm-aui-assistant-content, .sc-user-plain');
      if (!content || !root?.contains(content) || !content.contains(range.endContainer)) { dismiss(); return; }
      const viewport = content.closest('.tm-aui-viewport')?.getBoundingClientRect();
      const top = Math.max(0, viewport?.top ?? 0);
      const bottom = Math.min(window.innerHeight, viewport?.bottom ?? window.innerHeight);
      const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0
        && rect.top >= top && rect.bottom <= bottom && rect.right > 0 && rect.left < window.innerWidth);
      if (!rects.length) { dismiss(); return; }
      // Inline markup can split the first selected line into several rectangles.
      const first = rects[0];
      const right = Math.max(...rects.filter((rect) => Math.abs(rect.top - first.top) < 2).map((rect) => rect.right));
      setSelection({ text, right, top: first.top, bottom: first.bottom });
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(update); };
    const pointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && popoverRef.current?.contains(event.target)) return;
      selecting = true;
      dismiss();
    };
    const pointerUp = () => { selecting = false; schedule(); };
    const cancel = () => { selecting = false; dismiss(); };
    const keyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') dismiss(); };
    const keyUp = (event: KeyboardEvent) => { if (event.key !== 'Escape') schedule(); };
    document.addEventListener('selectionchange', schedule);
    document.addEventListener('pointerdown', pointerDown);
    document.addEventListener('pointerup', pointerUp);
    document.addEventListener('pointercancel', cancel);
    document.addEventListener('keydown', keyDown);
    document.addEventListener('keyup', keyUp);
    window.addEventListener('scroll', dismiss, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('blur', cancel);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('selectionchange', schedule);
      document.removeEventListener('pointerdown', pointerDown);
      document.removeEventListener('pointerup', pointerUp);
      document.removeEventListener('pointercancel', cancel);
      document.removeEventListener('keydown', keyDown);
      document.removeEventListener('keyup', keyUp);
      window.removeEventListener('scroll', dismiss, true);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('blur', cancel);
    };
  }, []);

  useLayoutEffect(() => {
    const popover = popoverRef.current;
    if (!selection || !popover) return;
    const { width, height } = popover.getBoundingClientRect();
    const gap = 8;
    const left = Math.max(gap, Math.min(selection.right - width, window.innerWidth - width - gap));
    const preferredTop = selection.top - height - gap;
    const top = preferredTop >= gap ? preferredTop : Math.min(selection.bottom + gap, window.innerHeight - height - gap);
    popover.style.left = `${left}px`;
    popover.style.top = `${Math.max(gap, top)}px`;
  }, [selection]);

  return selection && createPortal(
    <div ref={popoverRef} className="sc-selection-popover">
      <button className="sc-selection-quote" type="button" aria-label="引用所选文字继续追问"
        onPointerDown={(event) => event.preventDefault()}
        onClick={() => { onQuote(selection.text); setSelection(null); window.getSelection()?.removeAllRanges(); }}>
        <Quote size={14} aria-hidden="true" />引用追问
      </button>
    </div>, document.body,
  );
}
