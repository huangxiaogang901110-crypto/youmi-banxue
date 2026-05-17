"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { GraduationCap, ClipboardList } from "lucide-react";
import Link from "next/link";
import ActivationModal from "@/components/entitlement/ActivationModal";
import { getToken } from "@/lib/api";

const STORAGE_KEY = "yomi_homework_subjects";
const STORAGE_DONE_KEY = "yomi_homework_done";

export default function HomePage() {
  const [showActivation, setShowActivation] = useState(false);
  const [homeworkProgress, setHomeworkProgress] = useState<{
    total: number;
    done: number;
  } | null>(null);

  const router = useRouter();

  // 首页分流：新用户→注册 / 老用户→登录 / 已登录→正常。
  // sessionStorage 检测新会话——浏览器被杀进程重开视为登出。
  useEffect(() => {
    const SESSION_KEY = "yomi_session";
    const isNewSession = !sessionStorage.getItem(SESSION_KEY);

    if (isNewSession) {
      sessionStorage.setItem(SESSION_KEY, "1");
      localStorage.removeItem("yomi_token");
    }

    const token = getToken();
    if (token) return; // 已登录，正常展示首页

    const VISITED_KEY = "yomi_has_visited";
    const hasVisited = localStorage.getItem(VISITED_KEY);
    if (!hasVisited) {
      localStorage.setItem(VISITED_KEY, "1");
      router.replace("/register");
    } else {
      router.replace("/login");
    }
  }, [router]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const doneRaw = localStorage.getItem(STORAGE_DONE_KEY);
      if (!raw) return;
      const subjects = JSON.parse(raw);
      const doneMap: Record<string, boolean> = doneRaw ? JSON.parse(doneRaw) : {};
      const total = (subjects as any[]).reduce(
        (sum: number, s: any) => sum + (s.tasks?.length || 0),
        0
      );
      const done = Object.values(doneMap).filter(Boolean).length;
      if (total > 0) setHomeworkProgress({ total, done });
    } catch {}
  }, []);

  return (
    <>
      <div className="flex items-start justify-center px-4 pt-12 animate-in fade-in duration-700">
        <div className="max-w-sm w-full space-y-4">
          {/* 主卡片 */}
          <div className="bg-card rounded-2xl shadow-sm p-8 text-center space-y-6">
            <div className="flex justify-center">
              <GraduationCap className="text-primary" size={64} strokeWidth={1.5} />
            </div>
            <div className="space-y-2">
              <h1 className="text-2xl font-bold text-foreground">悠米伴学</h1>
              <p className="text-muted-foreground text-sm">让每道题都有人讲</p>
            </div>
            <Link
              href="/workspace"
              className="inline-block w-full rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-90"
            >
              进入学习工作台
            </Link>
            <Link
              href="/upload"
              className="inline-block w-full rounded-xl border border-primary px-6 py-3 text-sm font-medium text-primary text-center transition hover:bg-primary/5"
            >
              识别作业清单
            </Link>
            <button
              onClick={() => setShowActivation(true)}
              className="text-xs text-primary underline hover:opacity-80"
            >
              激活码兑换
            </button>
          </div>

          {/* 今日作业进度卡片 */}
          {homeworkProgress && (
            <Link
              href="/upload"
              className="block bg-card rounded-2xl shadow-sm border border-border p-4 hover:border-primary/40 transition"
            >
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-primary/10 flex items-center justify-center shrink-0">
                  <ClipboardList className="w-5 h-5 text-primary" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground">今日作业</p>
                  <p className="text-xs text-muted-foreground">
                    {homeworkProgress.done}/{homeworkProgress.total} 已完成
                  </p>
                </div>
                <div className="text-right">
                  <span className="text-lg font-bold text-primary">
                    {Math.round((homeworkProgress.done / homeworkProgress.total) * 100)}%
                  </span>
                </div>
              </div>
              <div className="mt-3 w-full h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all"
                  style={{
                    width: `${(homeworkProgress.done / homeworkProgress.total) * 100}%`,
                  }}
                />
              </div>
            </Link>
          )}

          <p className="text-xs text-muted-foreground text-center">
            内测体验中 · 无需登录
          </p>
        </div>
      </div>

      <ActivationModal
        open={showActivation}
        onClose={() => setShowActivation(false)}
      />
    </>
  );
}
