"use client";

import { useState, useEffect, useRef } from "react";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Send, Lightbulb, List, Eye, CheckCircle, Bookmark, Loader2, Scan } from "lucide-react";
import { useTypewriter } from "@/hooks/useTypewriter";
import { tutorApi, visionApi, questionApi } from "@/lib/api";
import { useEntitlementStore } from "@/stores/entitlementStore";
import { saveTutorResult, loadTutorResult, saveVisionResult, loadVisionResult } from "@/lib/localCache";
import KaTeXText from "@/components/tutoring/KaTeXText";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import type { TutorResponse } from "@/lib/types";

const VISION_KEYWORDS = ["图", "图中", "线段", "表格", "几何", "箭头", "阴影", "方格", "坐标", "这条", "这个图"];

interface Message { role: "ai" | "user"; text: string }

function QuestionPageInner() {
  const searchParams = useSearchParams();
  const id = searchParams.get("qid") || "";
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [visionLoading, setVisionLoading] = useState(false);
  const [error, setError] = useState("");
  const [questionText, setQuestionText] = useState("");
  const [restored, setRestored] = useState(false);
  const [currentReply, setCurrentReply] = useState("");
  const [marked, setMarked] = useState<"none" | "mastered" | "mistake">("none");
  const msgEndRef = useRef<HTMLDivElement>(null);

  const { displayed, isComplete, skip } = useTypewriter({
    text: currentReply,
    speed: 45,
    enabled: currentReply.length > 0,
  });

  // ── Restore cached results on mount ──
  useEffect(() => {
    (async () => {
      try {
        const cached = await loadTutorResult(id);
        const cachedVision = await loadVisionResult(id);
        if (cached) {
          const r = cached as TutorResponse;
          setMessages([{ role: "ai", text: r.reply_text }]);
          setCurrentReply("");
        }
        if (cachedVision) {
          const v = cachedVision as TutorResponse;
          setMessages((prev) => [...prev, { role: "ai", text: `🔍 视觉重读：${v.reply_text}` }]);
        }
        const qResp = await questionApi.getDetail(id);
        if (qResp.ok && qResp.data) setQuestionText(qResp.data.question_text);
        setRestored(true);
      } catch { setRestored(true); }
    })();
  }, [id]);

  // ── Auto-scroll ──
  useEffect(() => { msgEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, displayed]);

  const creditBalance = useEntitlementStore((s) => s.creditBalance);
  const deductLocal = useEntitlementStore((s) => s.deductLocal);

  const doTutor = async (mode: "initial" | "followup", message: string) => {
    if (creditBalance <= 0) { setError("额度不足，请兑换激活码"); return; }
    setLoading(true);
    setError("");
    try {
      const resp = await tutorApi.send(id, { mode, message });
      if (!resp.ok || !resp.data) throw new Error("辅导请求失败");
      const text = resp.data.reply_text;
      setCurrentReply(text);
      await saveTutorResult(id, resp.data);
      deductLocal(1);
      return text;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "网络错误";
      setError(msg);
      return "";
    } finally {
      setLoading(false);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const text = input.trim();
    setInput("");
    // Flush any in-progress typewriter reply to messages
    if (currentReply && displayed) {
      setMessages((prev) => [...prev, { role: "ai", text: displayed }]);
      setCurrentReply("");
    }
    setMessages((prev) => [...prev, { role: "user", text }]);

    const isVision = VISION_KEYWORDS.some((kw) => text.includes(kw));
    if (isVision) {
      if (creditBalance <= 0) { setError("额度不足，请兑换激活码"); return; }
      setVisionLoading(true);
      try {
        const vResp = await visionApi.retry(id);
        if (vResp.ok && vResp.data) {
          await saveVisionResult(id, vResp.data);
          deductLocal(1);
          setCurrentReply(`🔍 AI 正在仔细观察原图...\n\n${vResp.data.reply_text}`);
          setMessages((prev) => [...prev, { role: "ai", text: "" }]);
        }
      } catch { setError("视觉重读失败，请重试"); }
      finally { setVisionLoading(false); }
    } else {
      await doTutor("followup", text);
    }
  };

  if (!restored) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="w-8 h-8 text-primary animate-spin" /></div>;
  }

  return (
    <div className="flex flex-col min-h-0">
      {/* Header */}
      <div className="flex items-center gap-3 mb-3">
        <Link href="/workspace" className="text-muted-foreground hover:text-foreground shrink-0">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <span className="text-sm font-semibold text-foreground truncate">
          {questionText ? questionText.slice(0, 30) + "…" : "加载中…"}
        </span>
      </div>

      {/* Crop image placeholder */}
      <div className="bg-muted rounded-xl h-36 flex items-center justify-center mb-3 border border-border relative overflow-hidden">
        {visionLoading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-32 h-32 border-2 border-primary/30 rounded-full animate-ping absolute" />
            <Scan className="w-10 h-10 text-primary animate-pulse relative z-10" />
            <span className="absolute bottom-3 text-xs text-primary z-10">AI 正在仔细观察原图...</span>
          </div>
        ) : (
          <Eye className="w-8 h-8 text-muted-foreground" />
        )}
        <span className="absolute top-2 left-3 text-xs text-muted-foreground">题目区域</span>
      </div>

      {/* Messages */}
      <div className="flex-1 space-y-3 mb-3 min-h-[200px]">
        {messages.length === 0 && !currentReply && (
          <div className="text-center py-8">
            <p className="text-muted-foreground text-sm mb-3">选择一种辅导方式开始</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
              msg.role === "ai" ? "bg-primary/10 text-foreground rounded-tl-md" : "bg-muted text-foreground rounded-tr-md"
            }`}>
              {msg.text ? <KaTeXText text={msg.text} /> : null}
            </div>
          </div>
        ))}

        {/* Typewriter bubble */}
        {currentReply && (
          <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-tl-md bg-primary/10 px-4 py-3 text-sm leading-relaxed">
              <KaTeXText text={displayed} />
              {!isComplete && (
                <button onClick={skip} className="mt-2 text-xs text-primary underline">
                  跳过动画
                </button>
              )}
            </div>
          </div>
        )}
        <div ref={msgEndRef} />
      </div>

      {/* Error */}
      {error && <div className="mb-3"><ErrorDisplay message={error} onRetry={() => setError("")} /></div>}

      {/* Action buttons */}
      {messages.length === 0 && !currentReply && (
        <div className="flex gap-2 mb-3 flex-wrap">
          <button onClick={() => doTutor("initial", "请给我一点提示")} disabled={loading}
            className="flex items-center gap-1.5 rounded-full border border-border px-3 py-2 text-xs text-foreground hover:bg-muted transition disabled:opacity-50">
            <Lightbulb className="w-3.5 h-3.5 text-amber-500" /> 给我一点提示
          </button>
          <button onClick={() => doTutor("initial", "请分步讲解")} disabled={loading}
            className="flex items-center gap-1.5 rounded-full border border-border px-3 py-2 text-xs text-foreground hover:bg-muted transition disabled:opacity-50">
            <List className="w-3.5 h-3.5 text-primary" /> 分步讲给我听
          </button>
          <button onClick={() => doTutor("initial", "请给出完整解析")} disabled={loading}
            className="flex items-center gap-1.5 rounded-full bg-primary text-primary-foreground px-3 py-2 text-xs hover:opacity-90 transition disabled:opacity-50">
            <Eye className="w-3.5 h-3.5" /> 查看完整解析
          </button>
        </div>
      )}

      {/* Follow-up input */}
      {messages.length > 0 && (
        <div className="flex gap-2 mb-3">
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="追问……如：这个图是什么意思？"
            className="flex-1 rounded-xl border border-border bg-card px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-primary/50 transition" />
          <button onClick={handleSend} disabled={loading || !input.trim()}
            className="rounded-xl bg-primary text-primary-foreground px-4 py-2.5 hover:opacity-90 transition shrink-0 disabled:opacity-50">
            <Send className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Bottom actions */}
      <div className="flex gap-3 pb-2">
        <button onClick={() => setMarked(marked === "mastered" ? "none" : "mastered")}
          className={`flex-1 flex items-center justify-center gap-1.5 rounded-xl border py-2.5 text-sm transition ${
            marked === "mastered" ? "border-green-400 bg-green-50 text-green-700" : "border-green-200 bg-green-50 text-green-700 hover:bg-green-100"
          }`}>
          <CheckCircle className="w-4 h-4" /> 我会了
        </button>
        <button onClick={() => setMarked(marked === "mistake" ? "none" : "mistake")}
          className={`flex-1 flex items-center justify-center gap-1.5 rounded-xl border py-2.5 text-sm transition ${
            marked === "mistake" ? "border-border bg-card text-foreground ring-1 ring-primary" : "border-border bg-card text-foreground hover:bg-muted"
          }`}>
          <Bookmark className="w-4 h-4" /> 加入错题本
        </button>
      </div>
    </div>
  );
}


export default function QuestionPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center py-20"><Loader2 className="w-8 h-8 text-primary animate-spin" /></div>}>
      <QuestionPageInner />
    </Suspense>
  );
}