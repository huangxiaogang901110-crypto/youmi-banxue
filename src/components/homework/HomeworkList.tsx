"use client";

import { Check, X, PartyPopper } from "lucide-react";
import type { HomeworkSubject } from "@/lib/types";

interface Props {
  subjects: HomeworkSubject[];
  doneMap: Record<string, boolean>;
  onToggle: (subjectIdx: number, taskIdx: number) => void;
  onDelete?: (subjectName: string, taskIdx: number) => void;
  /** 历史模式：不可编辑，不显示操作按钮 */
  readOnly?: boolean;
  /** 按日期标记用于 toggle/delete key 构造 */
  dateKey?: string;
}

export default function HomeworkList({
  subjects,
  doneMap,
  onToggle,
  onDelete,
  readOnly = false,
  dateKey = "",
}: Props) {
  const totalTasks = subjects.reduce((sum, s) => sum + s.tasks.length, 0);
  const doneCount = Object.values(doneMap).filter(Boolean).length;
  const pct = totalTasks > 0 ? Math.round((doneCount / totalTasks) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* 总进度条 */}
      <div className="bg-card rounded-2xl p-4 shadow-sm border border-border space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-foreground font-medium">完成进度</span>
          <span className="text-primary font-bold">
            {doneCount}/{totalTasks}
          </span>
        </div>
        <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-muted-foreground text-right">
          {pct === 100 ? <span><PartyPopper className="w-4 h-4 inline mr-1" /> 全部完成！</span> : `还剩 ${totalTasks - doneCount} 项`}
        </p>
      </div>

      {/* 按科目分组 */}
      {subjects.map((subject, si) => {
        const subjDone = subject.tasks.filter(
          (t, ti) => doneMap[`${subject.name}||${t.trim()}`]
        ).length;
        return (
          <div
            key={si}
            className="bg-card rounded-2xl shadow-sm border border-border overflow-hidden"
          >
            <div className="bg-muted/50 px-4 py-2.5 border-b border-border flex items-center justify-between">
              <div>
                <span className="text-sm font-semibold text-foreground">
                  {subject.name}
                </span>
                <span className="text-xs text-muted-foreground ml-2">
                  {subjDone}/{subject.tasks.length}
                </span>
              </div>
              {/* 科目进度小条 */}
              <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all"
                  style={{
                    width: `${
                      subject.tasks.length > 0
                        ? Math.round((subjDone / subject.tasks.length) * 100)
                        : 0
                    }%`,
                  }}
                />
              </div>
            </div>
            <ul className="divide-y divide-border">
              {subject.tasks.map((task, ti) => {
                const key = `${subject.name}||${task.trim()}`;
                const done = !!doneMap[key];
                return (
                  <li key={ti} className="flex items-center">
                    <button
                      onClick={() => onToggle(si, ti)}
                      disabled={readOnly}
                      className="flex-1 flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/30 transition disabled:opacity-60"
                    >
                      <span
                        className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                          done
                            ? "bg-green-400 border-green-400"
                            : "border-muted-foreground/30"
                        }`}
                      >
                        {done && (
                          <Check className="w-3 h-3 text-white" strokeWidth={3} />
                        )}
                      </span>
                      <span
                        className={`text-sm ${
                          done
                            ? "text-muted-foreground line-through"
                            : "text-foreground"
                        }`}
                      >
                        {task}
                      </span>
                    </button>
                    {!readOnly && onDelete && (
                      <button
                        onClick={() => onDelete(subject.name, ti)}
                        className="pr-3 text-muted-foreground hover:text-destructive transition shrink-0"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
