"use client";

import { useRef, useState, useEffect, useCallback } from "react";

export interface Bbox {
  question_id: string;
  bbox: [number, number, number, number]; // [x, y, w, h] in original coords
  question_number: number;
  is_correct?: boolean | null;
  answer_bbox?: [number, number, number, number] | null;
}

interface Props {
  bboxes: Bbox[];
  activeIndex: number;
  imageUrl?: string;
}

/** Green check SVG — placed at bottom-right of question bbox */
function GreenCheck({ left, top }: { left: string; top: string }) {
  return (
    <svg
      className="absolute pointer-events-none z-20"
      style={{ left, top, width: "24px", height: "24px", transform: "translate(-4px, -4px)" }}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="11" fill="#22c55e" stroke="white" strokeWidth="2" />
      <path d="M7 13l3 3 7-7" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Red circle SVG — placed at answer_bbox area */
function RedCircle({ left, top, width, height }: { left: string; top: string; width: string; height: string }) {
  return (
    <svg
      className="absolute pointer-events-none z-20"
      style={{ left, top, width, height }}
      viewBox="0 0 100 100"
      fill="none"
    >
      <ellipse cx="50" cy="50" rx="46" ry="46" stroke="#ef4444" strokeWidth="4" fill="none" />
    </svg>
  );
}

export default function BboxOverlay({ bboxes, activeIndex, imageUrl }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [dims, setDims] = useState({ dw: 0, dh: 0, ow: 1, oh: 1 });
  const [loaded, setLoaded] = useState(false);

  const calcDims = useCallback(() => {
    const img = imgRef.current;
    const container = containerRef.current;
    if (!img || !container) return;
    setDims({
      dw: img.clientWidth,
      dh: img.clientHeight,
      ow: img.naturalWidth || 1,
      oh: img.naturalHeight || 1,
    });
  }, []);

  useEffect(() => {
    const img = imgRef.current;
    if (!img) return;
    const observer = new ResizeObserver(calcDims);
    if (containerRef.current) observer.observe(containerRef.current);

    const onLoad = () => calcDims();
    if (img.complete) calcDims();
    else img.addEventListener("load", onLoad, { once: true });

    return () => {
      observer.disconnect();
      img.removeEventListener("load", onLoad);
    };
  }, [calcDims, imageUrl]);

  const mapCoord = (bbox: number[]) => {
    const [x, y, w, h] = bbox;
    return {
      left: `${(x / dims.ow) * 100}%`,
      top: `${(y / dims.oh) * 100}%`,
      width: `${(w / dims.ow) * 100}%`,
      height: `${(h / dims.oh) * 100}%`,
    };
  };

  return (
    <div ref={containerRef} className="relative w-full">
      {imageUrl ? (
        <img ref={imgRef} src={imageUrl} alt="作业" className="w-full rounded-xl" />
      ) : (
        <div className="text-xs text-muted-foreground text-center py-1">（题目区域 — 上传原图后可查看定位）</div>
      )}

      {/* Focus mask: dim non-active areas */}
      {activeIndex >= 0 && (
        <div className="absolute inset-0 bg-black/20 rounded-xl pointer-events-none" />
      )}

      {/* Correct/incorrect indicators (show for ALL, not just active) */}
      {bboxes.map((b) => {
        // Green check: at bottom-right of question bbox
        if (b.is_correct === true) {
          const qPos = mapCoord(b.bbox);
          return <GreenCheck key={`ok-${b.question_id}`} left={qPos.left} top={qPos.top} />;
        }
        // Red circle: at answer_bbox area
        if (b.is_correct === false && b.answer_bbox && b.answer_bbox[2] > 0 && b.answer_bbox[3] > 0) {
          const aPos = mapCoord(b.answer_bbox);
          return (
            <RedCircle
              key={`err-${b.question_id}`}
              left={aPos.left}
              top={aPos.top}
              width={aPos.width}
              height={aPos.height}
            />
          );
        }
        return null;
      })}

      {/* bbox rectangles — active question highlight */}
      {bboxes.map((b, i) => {
        const pos = mapCoord(b.bbox);
        const active = i === activeIndex;
        return (
          <div
            key={b.question_id}
            className={`absolute rounded-md border-2 transition-all pointer-events-none ${
              active
                ? "border-primary shadow-[0_0_8px_rgba(77,187,170,0.5)] z-10"
                : "border-transparent"
            }`}
            style={pos}
          >
            {active && (
              <span className="absolute -top-5 -left-0.5 text-xs bg-primary text-primary-foreground rounded px-1.5 py-0.5">
                {b.question_number}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
