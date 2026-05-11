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

    const onTouchStart = (e: TouchEvent) => {
      startX.current = e.touches[0].clientX;
    };

    const onTouchEnd = (e: TouchEvent) => {
      const delta = startX.current - e.changedTouches[0].clientX;
      if (delta > 40) {
        setOpen(true);
      } else if (delta < -10) {
        setOpen(false);
      } else if (open) {
        setOpen(false);
      } else if (onTap && Math.abs(delta) < 10) {
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
