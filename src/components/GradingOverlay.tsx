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

// ── V2 统一批改标注 ─────────────────────────────────────────────────────────
// 当 v2Marks 非空时前端优先渲染，旧 marks+groups 作为 fallback。
// 前端不自行推断批改语义，仅按 type 映射视觉样式。

export type GradingMarkType =
  | "group_box"    // 题组灰框
  | "group_label"  // 题组文字标签
  | "green_tick"   // 正确对勾
  | "red_circle"   // 错误红圈
  | "click_target"; // 可点击锚点（预留）

export interface GradingMark {
  mark_id: string;
  type: GradingMarkType;
  target_question_id?: string | null;
  target_group_id?: string | null;
  bbox: [number, number, number, number]; // [x, y, w, h] 原图坐标
  text?: string | null;
  style?: Record<string, string | number> | null;
  confidence?: number | null;
  source?: string | null;
}

interface Props {
  marks: OverlayMark[];
  groups: GroupBox[];
  imageUrl?: string;
  /** V2 统一 marks：非空时优先渲染，替代旧 marks+groups 逻辑。 */
  v2Marks?: GradingMark[];
  /** V2 needs_review 标志：为 true 时在 v2Marks 渲染路径上显示复查提示横幅。 */
  v2NeedsReview?: boolean;
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

export default function GradingOverlay({ marks, groups, imageUrl, v2Marks, v2NeedsReview }: Props) {
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
        v2Marks && v2Marks.length > 0 ? (
          // ── V2 渲染：统一 marks 列表，前端不推断批改语义 ──────────────────
          <>
            {/* needs_review 横幅：有部分题目需人工复查时提示 */}
            {v2NeedsReview && (
              <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 rounded-full bg-amber-50/90 border border-amber-300 px-3 py-1 text-xs text-amber-700 font-medium shadow-sm pointer-events-none">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" className="shrink-0">
                  <path d="M8 1.5L1 14.5h14L8 1.5z" stroke="#d97706" strokeWidth="1.5" strokeLinejoin="round" fill="none"/>
                  <line x1="8" y1="6.5" x2="8" y2="9.5" stroke="#d97706" strokeWidth="1.5" strokeLinecap="round"/>
                  <circle cx="8" cy="11.5" r="0.7" fill="#d97706"/>
                </svg>
                部分题目需人工复查
              </div>
            )}
            {v2Marks.map((m) => {
              const pos = mapCoord(m.bbox, dims.dw, dims.dh, ow, oh);

              if (m.type === "group_box") {
                return (
                  <div key={m.mark_id} className="absolute pointer-events-none" style={pos}>
                    <div className="absolute inset-0 rounded-xl border-2 border-gray-400/30 bg-gray-400/8" />
                  </div>
                );
              }

              if (m.type === "group_label") {
                return (
                  <div
                    key={m.mark_id}
                    className="absolute pointer-events-none text-[10px] text-gray-500 font-medium px-1 rounded bg-white/80 leading-5"
                    style={pos}
                  >
                    {m.text}
                  </div>
                );
              }

              if (m.type === "green_tick") {
                const tickSize = 32;
                const tickLeft = parseFloat(pos.left) + parseFloat(pos.width) - (tickSize / dims.dw) * 100;
                const tickTop = parseFloat(pos.top) + parseFloat(pos.height) - (tickSize / dims.dh) * 100;
                return (
                  <div
                    key={m.mark_id}
                    className="absolute pointer-events-none"
                    style={{ left: `${tickLeft}%`, top: `${tickTop}%` }}
                  >
                    <svg width={tickSize} height={tickSize} viewBox="0 0 32 32">
                      <path
                        d="M3 9 l4 4 l8 -8"
                        stroke="#22c55e"
                        strokeWidth="6"
                        fill="none"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                );
              }

              if (m.type === "red_circle") {
                const pad = 8;
                const pw = m.bbox[2] + pad * 2;
                const ph = m.bbox[3] + pad * 2;
                const circleLeft = parseFloat(pos.left) - (pad / dims.dw) * 100;
                const circleTop = parseFloat(pos.top) - (pad / dims.dh) * 100;
                return (
                  <div
                    key={m.mark_id}
                    className="absolute pointer-events-none"
                    style={{ left: `${circleLeft}%`, top: `${circleTop}%`, width: `${(pw / dims.dw) * 100}%`, height: `${(ph / dims.dh) * 100}%` }}
                  >
                    <svg width="100%" height="100%" viewBox={`0 0 ${pw} ${ph}`} preserveAspectRatio="none">
                      <ellipse
                        cx={pw / 2}
                        cy={ph / 2}
                        rx={pw / 2 - 2}
                        ry={ph / 2 - 2}
                        stroke="#ef4444"
                        strokeWidth="3"
                        fill="none"
                      />
                    </svg>
                  </div>
                );
              }

              if (m.type === "click_target") {
                // 预留：可点击锚点，暂渲染为蓝色小圆点
                return (
                  <div
                    key={m.mark_id}
                    className="absolute pointer-events-none flex items-center justify-center"
                    style={pos}
                  >
                    <div className="w-3 h-3 rounded-full bg-blue-400/50 border border-blue-500/50" />
                  </div>
                );
              }

              return null;
            })}
          </>
        ) : (
          // ── Legacy 渲染：旧 marks + groups（完整保留，默认路径） ───────────
          <>
            {/* Layer 2: 题组灰框 */}
            {groups.map((g) => {
              const pos = mapCoord(g.bbox, dims.dw, dims.dh, ow, oh);
              return (
                <div key={g.group_id} className="absolute pointer-events-none" style={pos}>
                  <div className="absolute inset-0 rounded-xl border-2 border-gray-400/30 bg-gray-400/8" />
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
                const tickSize = 32;
                const tickLeft = parseFloat(pos.left) + parseFloat(pos.width) - (tickSize / dims.dw) * 100;
                const tickTop = parseFloat(pos.top) + parseFloat(pos.height) - (tickSize / dims.dh) * 100;
                return (
                  <div
                    key={m.question_id}
                    className="absolute pointer-events-none"
                    style={{ left: `${tickLeft}%`, top: `${tickTop}%` }}
                  >
                    <svg width={tickSize} height={tickSize} viewBox="0 0 32 32">
                      <path
                        d="M3 9 l4 4 l8 -8"
                        stroke="#22c55e"
                        strokeWidth="6"
                        fill="none"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </div>
                );
              }

              if (isWrong) {
                // 红圈 — 只圈 answer_bbox，外扩 8px
                const pad = 8;
                const pw = m.mark_bbox[2] + pad * 2;
                const ph = m.mark_bbox[3] + pad * 2;
                const tickLeft = parseFloat(pos.left) - (pad / dims.dw) * 100;
                const tickTop = parseFloat(pos.top) - (pad / dims.dh) * 100;
                return (
                  <div key={m.question_id} className="absolute pointer-events-none" style={{ left: `${tickLeft}%`, top: `${tickTop}%`, width: `${(pw / dims.dw) * 100}%`, height: `${(ph / dims.dh) * 100}%` }}>
                    <svg
                      width="100%"
                      height="100%"
                      viewBox={`0 0 ${pw} ${ph}`}
                      preserveAspectRatio="none"
                    >
                      <ellipse
                        cx={pw / 2}
                        cy={ph / 2}
                        rx={pw / 2 - 2}
                        ry={ph / 2 - 2}
                        stroke="#ef4444"
                        strokeWidth="3"
                        fill="none"
                      />
                    </svg>
                  </div>
                );
              }

              return null;
            })}
          </>
        )
      )}
    </div>
  );
}
