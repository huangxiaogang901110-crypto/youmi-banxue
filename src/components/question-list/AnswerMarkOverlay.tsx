"use client";

import { useRef, useState, useEffect, useCallback } from "react";

export interface AnswerMark {
  question_id: string;
  is_correct: boolean;
  answer_bbox: [number, number, number, number]; // [x, y, w, h] in original coords
  question_number?: number;
}

interface Props {
  marks: AnswerMark[];
  imageUrl?: string;
  onMarkClick?: (questionId: string) => void;
}

const CHECK_SIZE = 18;

export default function AnswerMarkOverlay({ marks, imageUrl, onMarkClick }: Props) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [dims, setDims] = useState({ ow: 1, oh: 1 });
  const [imgLoaded, setImgLoaded] = useState(false);

  const calcDims = useCallback(() => {
    const img = imgRef.current;
    if (!img) return;
    setDims({
      ow: img.naturalWidth || 1,
      oh: img.naturalHeight || 1,
    });
    setImgLoaded(true);
  }, []);

  useEffect(() => {
    const img = imgRef.current;
    if (!img || !imageUrl) return;
    if (img.complete) calcDims();
    else img.addEventListener("load", calcDims, { once: true });
  }, [calcDims, imageUrl]);

  const validMarks = marks.filter((m) => {
    if (!m.answer_bbox || m.answer_bbox.length !== 4) return false;
    const [x, y, w, h] = m.answer_bbox;
    return [x, y, w, h].every((v) => Number.isFinite(v)) && w > 0 && h > 0;
  });

  if (!imageUrl || validMarks.length === 0) return null;

  return (
    <div className="absolute inset-0 pointer-events-none">
      {/* Hidden image for dimension tracking */}
      <img
        ref={imgRef}
        src={imageUrl}
        alt=""
        className="absolute opacity-0 w-full h-auto"
        onLoad={calcDims}
      />

      {imgLoaded &&
        validMarks.map((mark) => {
          const [x, y, w, h] = mark.answer_bbox;
          const leftPct = `${(x / dims.ow) * 100}%`;
          const topPct = `${(y / dims.oh) * 100}%`;
          const widthPct = `${(w / dims.ow) * 100}%`;
          const heightPct = `${(h / dims.oh) * 100}%`;

          if (mark.is_correct) {
            const checkLeftPct = `${((x + w - CHECK_SIZE) / dims.ow) * 100}%`;
            const checkTopPct = `${((y + h - CHECK_SIZE) / dims.oh) * 100}%`;

            return (
              <svg
                key={mark.question_id}
                className="absolute pointer-events-auto cursor-pointer"
                style={{
                  left: checkLeftPct,
                  top: checkTopPct,
                  width: `${CHECK_SIZE}px`,
                  height: `${CHECK_SIZE}px`,
                }}
                onClick={() => onMarkClick?.(mark.question_id)}
                viewBox="0 0 18 18"
              >
                <circle cx="9" cy="9" r="8" fill="#22c55e" />
                <path
                  d="M5 9.5l3 3 5-6"
                  fill="none"
                  stroke="white"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            );
          }

          return (
            <svg
              key={mark.question_id}
              className="absolute pointer-events-auto cursor-pointer"
              style={{
                left: leftPct,
                top: topPct,
                width: widthPct,
                height: heightPct,
              }}
              onClick={() => onMarkClick?.(mark.question_id)}
              viewBox={`0 0 ${w} ${h}`}
            >
              <ellipse
                cx={w / 2}
                cy={h / 2}
                rx={w / 2 + 6}
                ry={h / 2 + 6}
                fill="none"
                stroke="#ef4444"
                strokeWidth="2"
              />
            </svg>
          );
        })}
    </div>
  );
}
