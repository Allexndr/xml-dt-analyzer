import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

type Props = {
  left: ReactNode;
  right: ReactNode;
  storageKey: string;
  leftLabel: string;
  minLeft?: number;
  minRight?: number;
};

const STEP = 16;

function readStored(key: string): number | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: number) {
  try {
    window.localStorage.setItem(key, String(Math.round(value)));
  } catch {
    /* хранилище недоступно */
  }
}

export default function SplitPane({ left, right, storageKey, leftLabel, minLeft = 280, minRight = 320 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(() => readStored(storageKey));
  const [total, setTotal] = useState(0);
  const dragging = useRef(false);

  const clamp = useCallback(
    (w: number, t: number) => {
      const max = Math.max(minLeft, t - minRight - 12);
      return Math.min(Math.max(w, minLeft), max);
    },
    [minLeft, minRight],
  );

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setTotal(el.clientWidth));
    ro.observe(el);
    setTotal(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const effective = total > 0 ? clamp(width ?? total * 0.55, total) : (width ?? 0);

  const commit = (w: number) => {
    const next = clamp(w, total);
    setWidth(next);
    writeStored(storageKey, next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      commit(effective - STEP);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      commit(effective + STEP);
    } else if (e.key === "Home") {
      e.preventDefault();
      commit(0);
    } else if (e.key === "End") {
      e.preventDefault();
      commit(total);
    }
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current || !box.current) return;
    const rect = box.current.getBoundingClientRect();
    setWidth(clamp(e.clientX - rect.left - 6, total));
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (box.current) {
      const rect = box.current.getBoundingClientRect();
      commit(e.clientX - rect.left - 6);
    }
  };

  const pct = total > 0 ? Math.round((effective / total) * 100) : 50;

  return (
    <div
      ref={box}
      className="split"
      style={effective > 0 ? ({ "--split-left": `${effective}px` } as React.CSSProperties) : undefined}
    >
      <div className="split__pane">{left}</div>
      <div
        className="split__handle"
        role="separator"
        aria-orientation="vertical"
        aria-label={`Граница панелей. Ширина панели «${leftLabel}»`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct}% ширины`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
      <div className="split__pane">{right}</div>
    </div>
  );
}
