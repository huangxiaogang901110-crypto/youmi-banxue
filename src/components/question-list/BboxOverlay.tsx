"use client";

import { useRef, useState, useEffect, useCallback } from "react";

export interface Bbox {
  question_id: string;
  bbox: [number, number, number, number]; // [x, y, w, h] in original coords
  question_number: number;
}

interface Props {
  bboxes: Bbox[];
  activeIndex: number;
  imageUrl?: string;
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

  const isRenderableBbox = (bbox: number[]) => {
    if (!Array.isArray(bbox) || bbox.length !== 4) return false;
    const [x, y, w, h] = bbox;
    return [x, y, w, h].every((v) => Number.isFinite(v)) && w > 0 && h > 0;
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

      {/* bbox rectangles */}
      {bboxes.map((b, i) => {
        if (!isRenderableBbox(b.bbox)) return null;
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
