"use client";

import { useEffect, useState, useRef } from "react";
import { CheckCircle2, Loader2, Scan, FileSearch, Brain, ListChecks, MessageSquareText } from "lucide-react";

const stages = [
  { key: "uploaded", label: "已收到作业", Icon: CheckCircle2 },
  { key: "enhancing", label: "正在优化清晰度", Icon: Scan },
  { key: "ocr_running", label: "正在识别文字", Icon: FileSearch },
  { key: "cutting", label: "正在拆分题目", Icon: ListChecks },
  { key: "vision_reviewing", label: "正在分析题目", Icon: Brain },
  { key: "schema_validating", label: "正在批改题目", Icon: MessageSquareText },
  { key: "completed", label: "整理完成", Icon: CheckCircle2 },
];

// 每阶段虚拟耗时（ms），用于自动推进
const STAGE_MS = [3000, 3000, 5000, 8000, 10000, 10000, 10000];

// backend status → stage index
const STATUS_TO_STAGE: Record<string, number> = {
  created: 0,
  uploaded: 0,
  enhancing: 1,
  ocr_running: 2,
  cutting: 3,
  vision_reviewing: 4,
  schema_validating: 5,
  completed: 6,
  needs_review: 6,
};

interface Props {
  status: string;
  questionsCount?: number;
}

export default function ProcessingStatus({ status, questionsCount }: Props) {
  const [stage, setStage] = useState(0);
  const baselineRef = useRef(0); // 后端已确认到达的最小 stage，只增不减

  useEffect(() => {
    // 后端终态 → completed 跳到最后，failed 保持当前 stage（页面接管报错）
    if (status === "completed") {
      const target = stages.length - 1;
      baselineRef.current = Math.max(baselineRef.current, target);
      setStage(target);
      return;
    }
    if (status === "failed") {
      // 不跳回第一步，停在当前阶段，页面切换 failed 视图会卸载本组件
      return;
    }

    // 后端当前阶段对应索引（未知状态保持当前基线，不回退）
    const backendStage = STATUS_TO_STAGE[status];
    if (backendStage !== undefined) {
      baselineRef.current = Math.max(baselineRef.current, backendStage);
    }

    let current = baselineRef.current;
    setStage(current);

    const advance = () => {
      current++;
      if (current >= stages.length - 1) {
        setStage(stages.length - 2); // 停在 completed 前，等后端通知
        return;
      }
      setStage(current);
      const delay = STAGE_MS[current] ?? 3000;
      timeoutId = setTimeout(advance, delay);
    };

    let timeoutId = setTimeout(advance, STAGE_MS[current] ?? 3000);

    return () => clearTimeout(timeoutId);
  }, [status]);

  return (
    <div className="bg-card rounded-2xl p-6 shadow-sm border border-border space-y-4">
      <p className="text-sm text-muted-foreground text-center">
        通常需要 20-30 秒，请保持页面打开
      </p>

      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all duration-700"
          style={{ width: `${((stage + 1) / stages.length) * 100}%` }}
        />
      </div>

      <div className="space-y-2">
        {stages.map((s, i) => {
          const done = i < stage;
          const current = i === stage;
          return (
            <div
              key={s.key}
              className={`flex items-center gap-3 text-sm transition-colors ${
                done ? "text-primary" : current ? "text-foreground" : "text-muted-foreground/50"
              }`}
            >
              {done ? (
                <CheckCircle2 className="w-4 h-4 shrink-0" />
              ) : current ? (
                <Loader2 className="w-4 h-4 shrink-0 animate-spin" />
              ) : (
                <s.Icon className="w-4 h-4 shrink-0 opacity-40" />
              )}
              <span>{s.label}</span>
            </div>
          );
        })}
      </div>

      {questionsCount != null && questionsCount > 0 && status === "completed" && (
        <p className="text-sm text-primary text-center font-medium">
          已识别 {questionsCount} 道题目
        </p>
      )}
    </div>
  );
}
