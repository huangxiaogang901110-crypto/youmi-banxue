"use client";

import { useRef, useEffect, useState, type ReactNode } from "react";

interface SwipeableCardProps {
  actions: { label: string; color: "red" | "blue"; onClick: () => void }[];
  children: ReactNode;
  onTap?: () => void;
  className?: string;
}

export default function SwipeableCard({ actions, children, onTap, className = "" }: SwipeableCardProps) {
  const [open, setOpen] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const startX = useRef(0);
  const offset = actions.length * 64;

  useEffect(() => {
    const el = cardRef.current;
    if (!el) return;

    let startY = 0;

    const onTouchStart = (e: TouchEvent) => {
      startX.current = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    };

    const onTouchEnd = (e: TouchEvent) => {
      // 触摸落在按钮/链接上 → 不触发 onTap，让元素的 click 处理
      const target = e.target as HTMLElement;
      if (target.closest("button, a, [role='button'], input, select, textarea")) return;

      const deltaX = startX.current - e.changedTouches[0].clientX;
      const deltaY = startY - e.changedTouches[0].clientY;
      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);

      // 垂直滚动：Y 主导且 Y > 12 → 不触发任何手势
      if (absY > 12 && absY > absX) return;

      // 左划展开：明显左移
      if (deltaX > 40) {
        setOpen(true);
      }
      // 右划关闭
      else if (deltaX < -10) {
        setOpen(false);
      }
      // 已展开时轻触关闭
      else if (open) {
        setOpen(false);
      }
      // tap：极小移动
      else if (onTap && absX < 8 && absY < 8) {
        onTap();
      }
    };

    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchend", onTouchEnd, { passive: true });

    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchend", onTouchEnd);
    };
  }, [open, onTap]);

  return (
    <div className={`relative overflow-hidden rounded-xl mb-2 ${className}`}>
      <div className="absolute right-0 top-0 bottom-0 flex rounded-r-xl overflow-hidden">
        {actions.map((a, i) => (
          <button
            key={i}
            onClick={(e) => {
              e.stopPropagation();
              a.onClick();
              setOpen(false);
            }}
            className={`w-16 flex items-center justify-center text-white text-sm font-medium ${
              a.color === "red" ? "bg-destructive" : "bg-blue-500"
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>

      <div
        ref={cardRef}
        className="relative bg-card rounded-xl p-4 shadow-sm border border-border transition-transform duration-200 ease-out select-none"
        style={{
          transform: open ? `translateX(-${offset}px)` : "translateX(0)",
          touchAction: "pan-y",
        }}
      >
        {children}
      </div>
    </div>
  );
}
