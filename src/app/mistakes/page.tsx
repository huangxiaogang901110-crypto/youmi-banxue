"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Trash2, AlertTriangle, BookOpen, ArrowRight } from "lucide-react";
import { mistakesApi } from "@/lib/api";
import type { MistakeItem } from "@/lib/types";
import ErrorDisplay from "@/components/common/ErrorDisplay";

export default function MistakesPage() {
  const [items, setItems] = useState<MistakeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const router = useRouter();

  useEffect(() => {
    (async () => {
      try {
        const resp = await mistakesApi.list();
        if (resp.ok && resp.data) setItems(resp.data);
        else setError(resp.message || "加载失败");
      } catch {
        setError("网络错误，请稍后重试");
      }
      setLoading(false);
    })();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-8">
        <ErrorDisplay message={error} onRetry={() => { setError(""); setLoading(true); window.location.reload(); }} />
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-4">
      <h1 className="text-lg font-bold text-foreground">错题本</h1>

      {items.length === 0 ? (
        <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-3">
          <BookOpen className="w-10 h-10 text-muted-foreground mx-auto" />
          <p className="text-muted-foreground text-sm">还没有错题记录</p>
          <p className="text-xs text-muted-foreground">辅导后点击"加入错题本"即可记录</p>
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((item) => (
            <div
              key={item.id}
              className="bg-card rounded-xl p-4 shadow-sm border border-border"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground truncate">{item.question_id}</p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {item.error_type_code === "unknown" ? "待复习" : item.error_type_code}
                    {" · "}
                    {item.created_at?.slice(0, 10) || ""}
                  </p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full ${
                  item.mastery_status === "pending"
                    ? "bg-warning/10 text-warning"
                    : "bg-primary/10 text-primary"
                }`}>
                  {item.mastery_status === "pending" ? "待复习" : "已掌握"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
