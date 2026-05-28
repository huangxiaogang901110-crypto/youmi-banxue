"use client";

import { useRef, useState, useEffect, useCallback } from "react";

export interface OverlayMark {
  question_id: string;
  mark_type: "correct" | "incorrect" | "unknown";
  mark_bbox: [number, number, number, number]; // [x, y, w, h]
  question_number: number;
}

export interface GroupBox {
  group_id: string;
  group_index: number;
  label: string;
  title?: string | null;
  bbox: [number, number, number, number]; // [x, y, w, h]
  question_ids: string[];
}

interface Props {
  marks: OverlayMark[];
  groups: GroupBox[];
  imageUrl?: string;
}

const mapCoord = (bbox: number[], dw: number, dh: number, ow: number, oh: number) => {
  const [x, y, w, h] = bbox;
  return {
    left: `${(x / ow) * 100}%`,
    top: `${(y / oh) * 100}%`,
    width: `${(w / ow) * 100}%`,
    height: `${(h / oh) * 100}%`,
  };
};

export default function GradingOverlay({ marks, groups, imageUrl }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [dims, setDims] = useState({ dw: 0, dh: 0, ow: 1, oh: 1 });

  const calcDims = useCallback(() => {
    const img = imgRef.current;
    if (!img) return;
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

  const { ow, oh } = dims;
  const hasOverlay = ow > 1;

  return (
    <div ref={containerRef} className="relative w-full">
      {imageUrl ? (
        <img ref={imgRef} src={imageUrl} alt="作业原图" className="w-full rounded-xl" />
      ) : (
        <div className="text-xs text-muted-foreground text-center py-8 rounded-xl bg-muted/30">
          暂无原图，请重新上传
        </div>
      )}

      {hasOverlay && (
        <>
          {/* Layer 2: 题组灰框 */}
          {groups.map((g) => {
            const pos = mapCoord(g.bbox, dims.dw, dims.dh, ow, oh);
            return (
              <div key={g.group_id} className="absolute pointer-events-none" style={pos}>
                <div className="absolute inset-0 rounded-xl border-2 border-gray-400/30 bg-gray-400/8" />
                {/* Layer 3: 黑色题号标签 */}
                <span className="absolute -left-1 top-0 -translate-x-full text-xs font-bold bg-black/75 text-white rounded-full w-5 h-5 flex items-center justify-center">
                  {g.label || g.group_index || "?"}
                </span>
              </div>
            );
          })}

          {/* Layer 4: 对勾 + 红圈 */}
          {marks.map((m) => {
            const isCorrect = m.mark_type === "correct";
            const isWrong = m.mark_type === "incorrect";
            if (!isCorrect && !isWrong) return null;

            const pos = mapCoord(m.mark_bbox, dims.dw, dims.dh, ow, oh);

            if (isCorrect) {
              // 绿色对勾 — 落在 answer_bbox 右下角
              const tickSize = 18;
              const tickLeft = parseFloat(pos.left) + parseFloat(pos.width) - (tickSize / dims.dw) * 100;
              const tickTop = parseFloat(pos.top) + parseFloat(pos.height) - (tickSize / dims.dh) * 100;
              return (
                <div
                  key={m.question_id}
                  className="absolute pointer-events-none"
                  style={{ left: `${tickLeft}%`, top: `${tickTop}%` }}
                >
                  <svg width={tickSize} height={tickSize} viewBox="0 0 18 18">
                    <path
                      d="M3 9 l4 4 l8 -8"
                      stroke="#22c55e"
                      strokeWidth="2.5"
                      fill="none"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </div>
              );
            }

            if (isWrong) {
              // 红圈 — 只圈 answer_bbox
              return (
                <div key={m.question_id} className="absolute pointer-events-none" style={pos}>
                  <svg
                    width="100%"
                    height="100%"
                    viewBox={`0 0 ${m.mark_bbox[2]} ${m.mark_bbox[3]}`}
                    preserveAspectRatio="none"
                  >
                    <ellipse
                      cx={m.mark_bbox[2] / 2}
                      cy={m.mark_bbox[3] / 2}
                      rx={m.mark_bbox[2] / 2 - 2}
                      ry={m.mark_bbox[3] / 2 - 2}
                      stroke="#ef4444"
                      strokeWidth="2"
                      fill="none"
                    />
                  </svg>
                </div>
              );
            }

            return null;
          })}
        </>
      )}
    </div>
  );
}
