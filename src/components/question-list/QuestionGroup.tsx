"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, ChevronUp, Lightbulb, BookOpen, Bookmark } from "lucide-react";
import type { Question } from "@/lib/types";
import { questionStatusApi, mistakesApi } from "@/lib/api";

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
  const MISTAKE_KEY = "yomi_mistake_ids";
  const [mistakeIds, setMistakeIds] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem(MISTAKE_KEY);
      return stored ? new Set(JSON.parse(stored)) : new Set();
    } catch { return new Set(); }
  });
  const router = useRouter();

  // 持久化到 localStorage
  useEffect(() => {
    localStorage.setItem(MISTAKE_KEY, JSON.stringify([...mistakeIds]));
  }, [mistakeIds]);

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
                {/* 判题结果 */}
                {q.student_answer != null && q.is_correct === true && (
                  <span className="text-xs text-green-600 font-medium shrink-0 mt-0.5">✓ 正确</span>
                )}
                {q.student_answer != null && q.is_correct === false && (
                  <span className="text-xs text-red-500 font-medium shrink-0 mt-0.5">✗ 错误</span>
                )}
                {q.student_answer != null && q.is_correct == null && (
                  <span className="text-xs text-muted-foreground shrink-0 mt-0.5">未判定</span>
                )}
                {q.student_answer == null && (
                  <span className="text-xs text-muted-foreground shrink-0 mt-0.5">未作答</span>
                )}
              </div>

              {/* 操作按钮 */}
              <div className="flex gap-2 pl-8">
                <button
                  onClick={() => router.push(`/question?qid=${q.question_id}&action=hint`)}
                  className="flex items-center justify-center gap-1 rounded-lg border border-border px-2.5 py-2 text-xs text-foreground hover:bg-muted transition">
                  <Lightbulb className="w-3.5 h-3.5 text-amber-500" />
                  提示
                </button>
                <button
                  onClick={() => router.push(`/question?qid=${q.question_id}&action=solve`)}
                  className="flex items-center justify-center gap-1 rounded-lg bg-primary text-primary-foreground px-2.5 py-2 text-xs font-medium hover:opacity-90 transition">
                  <BookOpen className="w-3.5 h-3.5" />
                  完整解析
                </button>
                <button
                  onClick={async () => {
                    const newSet = new Set(mistakeIds);
                    if (newSet.has(q.question_id)) {
                      // 取消：从错题本移除
                      newSet.delete(q.question_id);
                      setMistakeIds(newSet);
                      try {
                        const resp = await mistakesApi.list();
                        if (resp.ok && resp.data) {
                          const record = resp.data.find((m) => m.question_id === q.question_id);
                          if (record) await mistakesApi.delete(record.id);
                        }
                      } catch { /* 后端失败不影响本地状态 */ }
                    } else {
                      // 加入错题本
                      newSet.add(q.question_id);
                      setMistakeIds(newSet);
                      questionStatusApi.update(q.question_id, "mistake_book");
                    }
                  }}
                  className={`flex items-center justify-center gap-1 rounded-lg border px-2.5 py-2 text-xs transition ${
                    mistakeIds.has(q.question_id)
                      ? "border-primary bg-primary/10 text-primary ring-1 ring-primary/30"
                      : "border-border text-foreground hover:bg-muted"
                  }`}>
                  <Bookmark className={`w-3.5 h-3.5 ${mistakeIds.has(q.question_id) ? "text-primary" : ""}`} />
                  加入错题
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
