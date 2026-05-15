"use client";

import { useState, useEffect, useRef } from "react";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Send, Lightbulb, Eye, CheckCircle, Bookmark, Loader2, Scan } from "lucide-react";
import { useTypewriter } from "@/hooks/useTypewriter";
import { tutorApi, visionApi, questionApi } from "@/lib/api";
import { questionStatusApi } from "@/lib/api";
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
  const action = searchParams.get("action") || "";
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [visionLoading, setVisionLoading] = useState(false);
  const [error, setError] = useState("");
  const [questionText, setQuestionText] = useState("");
  const [restored, setRestored] = useState(false);
  const [currentReply, setCurrentReply] = useState("");
  const [marked, setMarked] = useState<"none" | "mastered" | "mistake">("none");
  const [studentAnswer, setStudentAnswer] = useState<string | null>(null);
  const [isCorrect, setIsCorrect] = useState<boolean | null>(null);
  const [gradingExplanation, setGradingExplanation] = useState<string | null>(null);
  const msgEndRef = useRef<HTMLDivElement>(null);
  const currentActionRef = useRef<"hint" | "solve">((action as "hint" | "solve") || "solve");

  const { displayed, isComplete, skip } = useTypewriter({
    text: currentReply,
    speed: 45,
    enabled: currentReply.length > 0,
  });

  // ── Restore cached results on mount ──
  useEffect(() => {
    (async () => {
      try {
        const currentAction = (action as "hint" | "solve") || "solve";
        currentActionRef.current = currentAction;
        const cached = await loadTutorResult(id, currentAction);
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
        if (qResp.ok && qResp.data) {
          setQuestionText(qResp.data.question_text);
          setStudentAnswer(qResp.data.student_answer || null);
          setIsCorrect(qResp.data.is_correct ?? null);
          setGradingExplanation(qResp.data.grading_explanation || null);
        }
        setRestored(true);
      } catch { setRestored(true); }
    })();
  }, [id, action]);

  // ── Auto-scroll ──
  useEffect(() => { msgEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, displayed]);

  // ── Auto-trigger tutoring from workspace action ──
  const triggeredRef = useRef(false);
  useEffect(() => {
    if (!restored || triggeredRef.current) return;
    const actionMap: Record<string, string> = {
      hint: "请给我一点提示",
      solve: "请给出完整解析",
    };
    const msg = actionMap[action];
    if (msg && messages.length === 0) {
      triggeredRef.current = true;
      currentActionRef.current = action as "hint" | "solve";
      doTutor("initial", msg, action as "hint" | "solve");
    }
  }, [restored, action, messages.length]);

  const creditBalance = useEntitlementStore((s) => s.creditBalance);
  const deductLocal = useEntitlementStore((s) => s.deductLocal);
  const setCreditBalance = useEntitlementStore((s) => s.setCreditBalance);

  const doTutor = async (mode: "initial" | "followup", message: string, tutorAction?: "hint" | "solve") => {
    if (creditBalance <= 0) { setError("额度不足，请兑换激活码"); return; }
    setLoading(true);
    setError("");
    try {
      const resp = await tutorApi.send(id, { mode, message, action: tutorAction });
      if (!resp.ok || !resp.data) throw new Error("辅导请求失败");
      const text = resp.data.reply_text;
      setCurrentReply(text);
      // 用当前 active action 做 key，区分 hint/solve 缓存
      const cacheAction = tutorAction || currentActionRef.current;
      await saveTutorResult(id, cacheAction, resp.data);
      // 用服务端真实余额替代硬编码递减（基准 §15）
      if (resp.data.credit_balance >= 0) {
        setCreditBalance(resp.data.credit_balance);
      } else {
        deductLocal(1);
      }
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

      {/* 判题结果 */}
      {studentAnswer && (
        <div className="rounded-xl border border-border p-3 mb-3 space-y-1.5 text-sm">
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">孩子答案：</span>
            <span className="font-medium">{studentAnswer}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">判题结果：</span>
            {isCorrect === true && <span className="text-green-600 font-medium">✓ 正确</span>}
            {isCorrect === false && <span className="text-red-500 font-medium">✗ 错误</span>}
            {isCorrect === null && <span className="text-muted-foreground">未判定</span>}
          </div>
          {gradingExplanation && (
            <p className="text-xs text-muted-foreground">{gradingExplanation}</p>
          )}
        </div>
      )}

      {/* Crop image / 题目文本 */}
      <div className="bg-muted rounded-xl min-h-[80px] flex items-center justify-center mb-3 border border-border relative overflow-hidden">
        {visionLoading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-32 h-32 border-2 border-primary/30 rounded-full animate-ping absolute" />
            <Scan className="w-10 h-10 text-primary animate-pulse relative z-10" />
            <span className="absolute bottom-3 text-xs text-primary z-10">AI 正在仔细观察原图...</span>
          </div>
        ) : questionText ? (
          <div className="px-4 py-3 w-full">
            <span className="absolute top-2 left-3 text-xs text-muted-foreground">题目区域</span>
            <p className="text-sm text-foreground leading-relaxed mt-4">{questionText}</p>
          </div>
        ) : (
          <Eye className="w-8 h-8 text-muted-foreground" />
        )}
        {!questionText && (
          <span className="absolute top-2 left-3 text-xs text-muted-foreground">题目区域</span>
        )}
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

      {/* Action buttons — 始终可见，已有回复时缩小尺寸 */}
      <div className="flex gap-2 mb-3 flex-wrap">
        <button
          onClick={() => {
            // 切换 action：清空当前内容并重新请求
            currentActionRef.current = "hint";
            setMessages([]);
            setCurrentReply("");
            doTutor("initial", "请给我一点提示", "hint");
          }}
          disabled={loading}
          className={`flex items-center gap-1.5 rounded-full border border-border px-3 py-2 text-xs text-foreground hover:bg-muted transition disabled:opacity-50 ${
            currentActionRef.current === "hint" && currentReply ? "ring-2 ring-primary/30" : ""
          }`}>
          <Lightbulb className="w-3.5 h-3.5 text-amber-500" /> 给我一点提示
        </button>
        <button
          onClick={() => {
            currentActionRef.current = "solve";
            setMessages([]);
            setCurrentReply("");
            doTutor("initial", "请给出完整解析", "solve");
          }}
          disabled={loading}
          className={`flex items-center gap-1.5 rounded-full px-3 py-2 text-xs transition disabled:opacity-50 ${
            currentActionRef.current === "solve" && currentReply
              ? "bg-primary text-primary-foreground ring-2 ring-primary/30"
              : "bg-primary text-primary-foreground hover:opacity-90"
          }`}>
          <Eye className="w-3.5 h-3.5" /> 查看完整解析
        </button>
      </div>

      {/* Follow-up input — 显示条件：有消息或有正在流式输出的回复 */}
      {(messages.length > 0 || !!currentReply) && (
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
        <button onClick={() => { setMarked(marked === "mastered" ? "none" : "mastered"); questionStatusApi.update(id, "mastered"); }}
          className={`flex-1 flex items-center justify-center gap-1.5 rounded-xl border py-2.5 text-sm transition ${
            marked === "mastered" ? "border-green-400 bg-green-50 text-green-700" : "border-green-200 bg-green-50 text-green-700 hover:bg-green-100"
          }`}>
          <CheckCircle className="w-4 h-4" /> 我会了
        </button>
        <button onClick={() => { setMarked(marked === "mistake" ? "none" : "mistake"); questionStatusApi.update(id, "mistake_book"); }}
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