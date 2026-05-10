"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, Scan, FileSearch, Brain, ListChecks } from "lucide-react";

const stages = [
  { key: "uploaded", label: "已收到作业", Icon: CheckCircle2 },
  { key: "enhancing", label: "正在优化清晰度", Icon: Scan },
  { key: "ocr_running", label: "正在识别文字", Icon: FileSearch },
  { key: "cutting", label: "正在拆分题目", Icon: ListChecks },
  { key: "vision_reviewing", label: "AI 复核图形完整性", Icon: Brain },
  { key: "completed", label: "整理完成", Icon: CheckCircle2 },
];

interface Props {
  status: string;
  questionsCount?: number;
}

export default function ProcessingStatus({ status, questionsCount }: Props) {
  const [stage, setStage] = useState(0);

  useEffect(() => {
    if (status === "completed" || status === "failed") {
      setStage(status === "completed" ? stages.length - 1 : -1);
      return;
    }
    const timer = setInterval(() => {
      setStage((s) => Math.min(s + 1, stages.length - 2));
    }, 4000);
    return () => clearInterval(timer);
  }, [status]);

  return (
    <div className="bg-card rounded-2xl p-6 shadow-sm border border-border space-y-4">
      <p className="text-sm text-muted-foreground text-center">
        通常需要 20-30 秒，请保持页面打开
      </p>

      {/* Progress bar */}
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all duration-700"
          style={{ width: `${((stage + 1) / stages.length) * 100}%` }}
        />
      </div>

      {/* Stage list */}
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

      {questionsCount && status === "completed" && (
        <p className="text-sm text-primary text-center font-medium">
          已识别 {questionsCount} 道题目
        </p>
      )}
    </div>
  );
}
