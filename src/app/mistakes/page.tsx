"use client";

import { useState } from "react";
import { Trash2, AlertTriangle } from "lucide-react";
import { clearAllCache } from "@/lib/localCache";

const errorCodes: Record<string, string> = {
  "计算错误": "calc_error",
  "概念不清": "concept",
  "审题失误": "read_error",
  "其他": "other",
};
const errorReasons = ["计算错误", "概念不清", "审题失误", "其他"];

const mockItems = [
  { id: "m1", num: 1, preview: "计算下列各题：25 × 4 = ?", status: "wrong", code: "", reason: "" },
  { id: "m2", num: 2, preview: "一个长方形长 8cm，宽 5cm，求面积", status: "mastered", code: "calc_error", reason: "计算错误" },
  { id: "m3", num: 3, preview: "阅读下面短文，回答问题", status: "review", code: "", reason: "" },
  { id: "m4", num: 4, preview: "解方程：3x + 5 = 20", status: "wrong", code: "concept", reason: "概念不清" },
  { id: "m5", num: 5, preview: "写出下列单词的中文意思", status: "mastered", code: "", reason: "" },
];

const statusLabel: Record<string, string> = {
  wrong: "错题",
  review: "待复习",
  mastered: "已掌握",
};

const statusColor: Record<string, string> = {
  wrong: "bg-red-50 text-red-600",
  review: "bg-amber-50 text-amber-600",
  mastered: "bg-green-50 text-green-600",
};

export default function MistakesPage() {
  const [showClear, setShowClear] = useState(false);

  return (
    <div className="space-y-6 pb-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-foreground">学习记录</h1>
        <button
          onClick={() => setShowClear(true)}
          className="text-xs text-muted-foreground hover:text-destructive transition flex items-center gap-1"
        >
          <Trash2 className="w-3.5 h-3.5" /> 删除本次记录
        </button>
      </div>

      {showClear && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm text-red-700 font-medium">确认删除？</p>
            <p className="text-xs text-red-600 mt-1">将删除本次所有学习记录和错题标记，不可恢复。</p>
            <div className="flex gap-2 mt-3">
              <button
                onClick={() => setShowClear(false)}
                className="rounded-lg bg-red-600 text-white px-4 py-1.5 text-xs font-medium hover:bg-red-700 transition"
              >
                确认删除
              </button>
              <button
                onClick={() => setShowClear(false)}
                className="rounded-lg border border-red-200 text-red-600 px-4 py-1.5 text-xs hover:bg-red-50 transition"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {mockItems.map((item) => (
          <div key={item.id} className="bg-card rounded-xl p-4 shadow-sm border border-border space-y-2">
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground w-8 shrink-0">#{item.num}</span>
              <span className="flex-1 text-sm text-foreground truncate">{item.preview}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full ${statusColor[item.status]}`}>
                {statusLabel[item.status]}
              </span>
            </div>

            {/* 错因记录 */}
            {item.status !== "mastered" && (
              <div className="flex gap-1.5 flex-wrap pl-11">
                {errorReasons.map((r) => (
                  <button
                    key={r}
                    className={`text-xs rounded-full px-2.5 py-0.5 border transition ${
                      item.reason === r
                        ? "border-primary text-primary bg-primary/10"
                        : "border-border text-muted-foreground hover:border-primary/30"
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 清空缓存 */}
      <button onClick={async () => { try { await clearAllCache(); setShowClear(false); } catch {} }} className="w-full rounded-xl border border-destructive/20 py-3 text-sm text-destructive hover:bg-destructive/5 transition">
        清空所有本地缓存
      </button>
    </div>
  );
}
