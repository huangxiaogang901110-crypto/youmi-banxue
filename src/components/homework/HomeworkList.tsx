"use client";

import { Check } from "lucide-react";
import type { HomeworkSubject } from "@/lib/types";

interface Props {
  subjects: HomeworkSubject[];
  doneMap: Record<string, boolean>;
  onToggle: (subjectIdx: number, taskIdx: number) => void;
}

export default function HomeworkList({ subjects, doneMap, onToggle }: Props) {
  const totalTasks = subjects.reduce((sum, s) => sum + s.tasks.length, 0);
  const doneCount = Object.values(doneMap).filter(Boolean).length;
  const pct = totalTasks > 0 ? Math.round((doneCount / totalTasks) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* 进度条 */}
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
          {pct === 100 ? "🎉 全部完成！" : `还剩 ${totalTasks - doneCount} 项`}
        </p>
      </div>

      {/* 按科目分组 */}
      {subjects.map((subject, si) => (
        <div
          key={si}
          className="bg-card rounded-2xl shadow-sm border border-border overflow-hidden"
        >
          <div className="bg-muted/50 px-4 py-2.5 border-b border-border">
            <span className="text-sm font-semibold text-foreground">
              {subject.name}
            </span>
            <span className="text-xs text-muted-foreground ml-2">
              {subject.tasks.filter((_, ti) => doneMap[`${si}-${ti}`]).length}/
              {subject.tasks.length}
            </span>
          </div>
          <ul className="divide-y divide-border">
            {subject.tasks.map((task, ti) => {
              const key = `${si}-${ti}`;
              const done = !!doneMap[key];
              return (
                <li key={ti}>
                  <button
                    onClick={() => onToggle(si, ti)}
                    className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/30 transition"
                  >
                    <span
                      className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                        done
                          ? "bg-primary border-primary"
                          : "border-muted-foreground/30"
                      }`}
                    >
                      {done && <Check className="w-3 h-3 text-white" strokeWidth={3} />}
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
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
