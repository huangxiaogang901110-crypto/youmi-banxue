"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Lightbulb, ListOrdered, BookOpen } from "lucide-react";
import type { Question } from "@/lib/types";

interface QuestionGroupProps {
  groupIndex: number;
  startNumber: number;
  endNumber: number;
  questions: Question[];
  defaultOpen?: boolean;
}

export default function QuestionGroup({
  groupIndex,
  startNumber,
  endNumber,
  questions,
  defaultOpen = false,
}: QuestionGroupProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="bg-card rounded-2xl shadow-sm border border-border overflow-hidden">
      {/* 组头 */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3.5 hover:bg-muted/30 transition"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground">
            第{startNumber}-{endNumber}题
          </span>
          <span className="text-xs text-muted-foreground">
            ({questions.length} 道)
          </span>
        </div>
        {open ? (
          <ChevronUp className="w-4 h-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        )}
      </button>

      {/* 展开内容 */}
      {open && (
        <div className="divide-y divide-border border-t border-border">
          {questions.map((q) => (
            <div key={q.question_id} className="px-4 py-3.5 space-y-3">
              {/* 题目 */}
              <div className="flex items-start gap-2">
                <span className="text-xs text-muted-foreground font-medium w-6 shrink-0 mt-0.5">
                  #{q.question_number}
                </span>
                <p className="text-sm text-foreground leading-relaxed flex-1">
                  {q.question_text || `(题目 #${q.question_number} — 识别中)`}
                </p>
              </div>

              {/* 操作按钮 */}
              <div className="flex gap-2 pl-8">
                <button className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-border py-2 text-xs text-foreground hover:bg-muted transition">
                  <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
                  给我一点提示
                </button>
                <button className="flex-1 flex items-center justify-center gap-1.5 rounded-lg border border-border py-2 text-xs text-foreground hover:bg-muted transition">
                  <ListOrdered className="w-3.5 h-3.5 text-blue-500" />
                  分步讲给我听
                </button>
                <button className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-primary text-primary-foreground py-2 text-xs font-medium hover:opacity-90 transition">
                  <BookOpen className="w-3.5 h-3.5" />
                  查看完整解析
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 计算最佳分组大小 */
export function calcGroupSize(total: number): number {
  if (total <= 0) return 5;
  if (total <= 10) return 5;
  if (total <= 24) return Math.ceil(total / 3);
  return Math.ceil(total / 4);
}

/** 将题目列表按 groupSize 分组 */
export function groupQuestions(questions: Question[], groupSize: number): Question[][] {
  const groups: Question[][] = [];
  for (let i = 0; i < questions.length; i += groupSize) {
    groups.push(questions.slice(i, i + groupSize));
  }
  return groups;
}
