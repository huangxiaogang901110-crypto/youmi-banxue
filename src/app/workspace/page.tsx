"use client";

import { Suspense, useState, useRef, useCallback, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Upload, ArrowRight, Clock, Loader2, X, Image, Camera, FileText } from "lucide-react";
import ProcessingStatus from "@/components/processing/ProcessingStatus";
import GradingOverlay from "@/components/GradingOverlay";
import type { OverlayMark, GroupBox } from "@/components/GradingOverlay";
import QuestionGroup, { calcGroupSize, groupQuestions } from "@/components/question-list/QuestionGroup";
import { useParseJobPolling } from "@/hooks/useParseJobPolling";
import { useJobHistory } from "@/hooks/useJobHistory";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import EmptyState from "@/components/common/EmptyState";
import SwipeableCard from "@/components/common/SwipeableCard";
import { compressImage } from "@/lib/imageCompress";
import { uuidv4 } from "@/lib/uuid";
import { authApi, parseJobApi } from "@/lib/api";
import { getToken } from "@/lib/api";
import type { QuestionSnapshot } from "@/lib/localCache";
import type { DocumentClassification, ParseJob, Question, RecentJob } from "@/lib/types";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

type UploadPhase = "idle" | "selected" | "compressing" | "initializing" | "uploading" | "recovering" | "error";

type DiagEvent = { ts: number; type: string; data: Record<string, unknown> };

function DiagPanel({ events, expanded, onToggle }: { events: DiagEvent[]; expanded: boolean; onToggle: () => void }) {
  const lines = events.map(e => {
    const elapsed = events.length > 1 ? `+${((e.ts - events[0].ts) / 1000).toFixed(1)}s` : "0s";
    const payload = Object.entries(e.data).map(([k,v]) => `${k}=${v}`).join(" ");
    return `${elapsed} ${e.type} ${payload}`;
  });
  const text = lines.join("\n");
  const copy = () => { navigator.clipboard.writeText(text).catch(() => {}); };

  return (
    <div className="text-[10px] font-mono bg-slate-900 text-green-400 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-1.5 bg-slate-800 cursor-pointer" onClick={onToggle}>
        <span>🔍 诊断日志 ({events.length}) {!expanded && events.length > 0 ? events[events.length-1].type : ''}</span>
        <div className="flex gap-2">
          <button onClick={(e) => { e.stopPropagation(); copy(); }} className="text-[10px] bg-slate-700 px-2 py-0.5 rounded hover:bg-slate-600">复制</button>
          <span className="text-slate-500">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>
      {expanded && (
        <div className="px-3 py-1.5 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
          {text || "等待事件…"}
        </div>
      )}
    </div>
  );
}

const DOCUMENT_FAMILY_LABELS: Record<string, string> = {
  math_homework: "数学作业页",
  chinese_homework: "语文作业页",
  english_homework: "英语作业页",
  mixed_homework: "混合作业页",
  cover_or_instruction_page: "封面/说明页",
  non_homework: "非作业页",
  math_arithmetic: "数学计算页",
  math_word_problem: "数学应用题",
  math_vertical: "数学竖式题",
  math_comparison_logic: "数学比较/选择题",
  math_visual_concept: "数学概念题",
  chinese_language: "语文练习页",
  english_language: "英语练习页",
  unknown: "未识别题型",
};

const SUPPORT_LEVEL_LABELS: Record<NonNullable<DocumentClassification["support_level"]>, string> = {
  full: "稳定支持",
  partial: "部分支持",
  unsupported: "暂不支持",
};

function getDocumentFamilyLabel(docFamily?: string | null): string {
  if (!docFamily) return DOCUMENT_FAMILY_LABELS.unknown;
  return DOCUMENT_FAMILY_LABELS[docFamily] || DOCUMENT_FAMILY_LABELS.unknown;
}

function getClassificationKey(classification?: DocumentClassification | null): string | null {
  return classification?.page_type || classification?.doc_family || null;
}

function getRecognitionHint(
  currentStatus: string,
  documentClassification?: DocumentClassification | null,
  questionCount: number = 0,
): { title: string; description: string } {
  const label = getDocumentFamilyLabel(getClassificationKey(documentClassification));
  const reason = documentClassification?.reason?.trim();
  const supportLevel = documentClassification?.support_level;

  if (supportLevel === "unsupported") {
    return {
      title: "当前页暂不支持自动批改",
      description: reason || `检测到这页更像${label}，当前链路会尽量识别内容，但不保证稳定批改结果。`,
    };
  }

  if (supportLevel === "partial") {
    if (currentStatus === "needs_review") {
      return {
        title: "这页需要人工核对",
        description: reason || `检测到这页更像${label}，当前只支持部分识别，建议结合原图人工复核。`,
      };
    }
    if (currentStatus === "low_confidence") {
      return {
        title: "结果仅供参考",
        description: reason || `检测到这页更像${label}，当前链路只能做部分识别，请逐题核对结果。`,
      };
    }
  }

  if (questionCount === 0) {
    return {
      title: "暂未识别出题目",
      description: reason || "这次识别没有返回可展示的题目。请重拍一张更清晰、更完整的作业后再试。",
    };
  }

  return {
    title: "识别结果置信度较低",
    description: reason || "请核对题目、答案和批改结果是否准确。",
  };
}

function RecognitionHintCard({
  status,
  classification,
  compact = false,
  questionCount = 0,
}: {
  status: string;
  classification?: DocumentClassification | null;
  compact?: boolean;
  questionCount?: number;
}) {
  const classificationKey = getClassificationKey(classification);
  if (!classification || !classificationKey || classificationKey === "unknown") {
    return null;
  }

  const hint = getRecognitionHint(status, classification, questionCount);
  const toneClass =
    classification.support_level === "unsupported"
      ? "bg-amber-50 border-amber-200 text-amber-900"
      : classification.support_level === "partial"
        ? "bg-sky-50 border-sky-200 text-sky-900"
        : "bg-emerald-50 border-emerald-200 text-emerald-900";

  return (
    <div className={`rounded-xl border px-4 ${compact ? "py-3" : "py-4"} ${toneClass}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{hint.title}</span>
        <span className="rounded-full bg-white/80 px-2.5 py-0.5 text-xs font-medium">
          {getDocumentFamilyLabel(classificationKey)}
        </span>
        <span className="rounded-full bg-white/80 px-2.5 py-0.5 text-xs">
          {SUPPORT_LEVEL_LABELS[classification.support_level]}
        </span>
      </div>
      <p className={`mt-2 ${compact ? "text-xs" : "text-sm"} opacity-90`}>
        {hint.description}
      </p>
    </div>
  );
}

function WorkspaceContent() {
  const { job, questions, status, error, overlay, group_boxes } = useParseJobPolling();
  const searchParams = useSearchParams();
  const [activeIndex, setActiveIndex] = useState(-1);
  const router = useRouter();
  const documentClassification = (job as ParseJob | null)?.document_classification || null;

  // ── 拍照上传状态 ──
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [uploadError, setUploadError] = useState("");
  const [uploadJobId, setUploadJobId] = useState<string | null>(null);  // 当前上传的 job_id，用于重试
  const [compressInfo, setCompressInfo] = useState("");
  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  // ── 7 天滚动历史缓存 ──
  const { history, upsert, removeEntry, clearAll } = useJobHistory();

  // ── 删除墓碑：本地持久化已删 job_id，防刷新/切模块后恢复 ──
  const DELETED_IDS_KEY = "youmi_deleted_parse_job_ids";
  const getDeletedJobIds = (): Set<string> => {
    try {
      const raw = localStorage.getItem(DELETED_IDS_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch { return new Set(); }
  };
  const markDeletedJobId = (jobId: string) => {
    try {
      const ids = getDeletedJobIds();
      ids.add(jobId);
      localStorage.setItem(DELETED_IDS_KEY, JSON.stringify([...ids]));
    } catch { /* quota exceeded */ }
  };

  // ── 上传待恢复：本地持久化 pending upload，防页面离开后丢失 ──
  const PENDING_KEY = "youmi_pending_upload";
  const getPendingUpload = (): { client_upload_id: string; created_at: string; file_name: string } | null => {
    try {
      const raw = localStorage.getItem(PENDING_KEY);
      if (!raw) return null;
      const p = JSON.parse(raw);
      if (!p.client_upload_id || !p.created_at) return null;
      // 超过 10 分钟过期
      if (Date.now() - new Date(p.created_at).getTime() > 10 * 60 * 1000) {
        localStorage.removeItem(PENDING_KEY);
        return null;
      }
      return p;
    } catch { return null; }
  };
  const setPendingUpload = (clientUploadId: string, fileName: string) => {
    try {
      localStorage.setItem(PENDING_KEY, JSON.stringify({
        client_upload_id: clientUploadId,
        created_at: new Date().toISOString(),
        file_name: fileName,
      }));
    } catch { /* quota */ }
  };
  const clearPendingUpload = () => {
    try { localStorage.removeItem(PENDING_KEY); } catch { /* ignore */ }
  };

  // ── API 补充（localStorage 清除/损坏后的恢复）──
  const [apiRecent, setApiRecent] = useState<RecentJob[]>([]);

  // ── 恢复中（防止闪烁）──
  const [isRestoring, setIsRestoring] = useState(false);

  // ── 当前孩子名 ──
  const [childName, setChildName] = useState("");

  // ── 诊断事件日志（仅 ?debug=1 时启用 UI 面板）──
  const showDebug = searchParams.get("debug") === "1";
  const [diagEvents, setDiagEvents] = useState<DiagEvent[]>([]);
  const [diagExpanded, setDiagExpanded] = useState(false);
  const addDiagEvent = useCallback((type: string, data: Record<string, unknown> = {}) => {
    const event = { ts: Date.now(), type, data };
    setDiagEvents(prev => [...prev.slice(-19), event]);
    console.log(`[diag] ${type}`, data);
  }, []);

  // 页面挂载时从后端拉取历史记录（补充 localStorage 可能丢失的数据）
  useEffect(() => {
    parseJobApi.getRecent().then((res) => {
      if (res.ok && res.data) setApiRecent(res.data);
    }).catch(() => {});
    // 获取当前孩子名
    authApi.children().then(res => {
      if (res.ok && res.data) {
        const token = getToken();
        if (token) {
          try {
            const payload = JSON.parse(atob(token.split(".")[1]));
            const active = res.data.find((c: {id:string;name:string}) => c.id === payload.child_id);
            if (active) setChildName(active.name);
          } catch {}
        }
      }
    }).catch(() => {});
  }, []);

  // 删除处理：tombstone 先写 → 清前端 → 后端 soft delete（双保险）
  const handleDelete = async (jobId: string) => {
    if (!jobId) return;
    // 1. 立即写 tombstone（即使后端失败，刷新也不恢复）
    markDeletedJobId(jobId);
    // 2. 立即清前端所有状态
    if (history.some(h => h.job_id === jobId)) {
      removeEntry(jobId);
    }
    setApiRecent(prev => prev.filter(r => r.job_id !== jobId));
    // 3. 调后端删除（成功验证，失败 console.error，tombstone 不回滚）
    try {
      const res = await parseJobApi.deleteJob(jobId);
      if (!res.ok) {
        console.error('后端删除失败', res.code, res.message);
      }
    } catch {
      console.error('后端删除请求失败（网络错误）');
    }
  };




  // ── pending upload 自动恢复（挂载时 / recovering 状态 / 页面回到前台）──
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tryRecover = async (clientUploadId: string, attempt: number) => {
      if (cancelled || attempt > 60) return;
      if (attempt === 0) addDiagEvent("recover_start", { cu: clientUploadId.slice(0, 12) });
      try {
        const res = await parseJobApi.recoverByUploadId(clientUploadId);
        if (cancelled) return;
        if (res.ok && res.data) {
          const j = res.data;
          addDiagEvent("recover_success", { jid: j.job_id, status: String(j.status), attempts: attempt + 1 });
          if (j.questions_count > 0) {
            addDiagEvent("questions_received", { source: "recover", jid: j.job_id.slice(0,8), qcount: j.questions_count });
          }
          clearPendingUpload();
          setPhase("idle");
          setUploadError("");
          upsert({
            job_id: j.job_id,
            file_name: j.file_name || file?.name || "",
            questions_count: j.questions_count || 0,
            status: j.status === "failed" ? "failed" : "uploaded",
            created_at: new Date().toISOString(),
          });
          router.push(`/workspace?job_id=${j.job_id}`);
          return;
        }
      } catch { /* continue */ }
      if (!cancelled && attempt < 60) {
        timer = setTimeout(() => tryRecover(clientUploadId, attempt + 1), 2000);
      } else if (!cancelled) {
        clearPendingUpload();
        setUploadError("暂未找到解析任务，请稍后重试");
        setPhase("error");
      }
    };

    const startRecovery = () => {
      const pending = getPendingUpload();
      if (pending) {
        if (phase !== "error") {
          setPhase("recovering");
          setUploadError("");
        }
        tryRecover(pending.client_upload_id, 0);
      }
    };

    startRecovery();

    const onVisible = () => {
      if (document.visibilityState === "visible") startRecovery();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [phase, file, router, upsert]);
  useEffect(() => {
    if (status === "polling" || status === "failed" || status === "completed" || status === "low_confidence" || status === "needs_review") {
      setIsRestoring(false);
    }
  }, [status]);

  // ── 诊断：phase 变化日志 ──
  useEffect(() => {
    addDiagEvent("phase_change", { phase, status, ujid: uploadJobId?.slice(0, 8) || "-" });
  }, [phase, status, uploadJobId]);

  // ── 诊断：poll 状态变化日志 ──
  const prevStatusRef = useRef(status);
  useEffect(() => {
    if (prevStatusRef.current !== status) {
      const prev = prevStatusRef.current;
      prevStatusRef.current = status;
      addDiagEvent(status === "polling" && prev === "loading" ? "poll_start" : "poll_status", { from: prev, to: status, jid: job?.job_id?.slice(0, 8) || "-", qcount: questions?.length ?? 0 });
    }
  }, [status, job, questions, error]);

  // 页面 idle：尝试恢复上次任务（uploaded 超过 5 分钟视为过期，不恢复）
  useEffect(() => {
    if (status !== "idle") return;
    if (history.length > 0) {
      const last = history[0];
      if (last.status !== "completed" && last.status !== "failed") {
        const age = Date.now() - new Date(last.created_at).getTime();
        if (last.status === "uploaded" && age > 5 * 60 * 1000) return; // >5min stale
        if (age < 24 * 60 * 60 * 1000) {
          setIsRestoring(true);
          router.replace(`/workspace?job_id=${last.job_id}`);
          return;
        }
      }
    }
  }, [status, router, history]);

  // 任务完成 → 更新历史 + 诊断 grading 字段
  useEffect(() => {
    if ((status === "completed" || status === "low_confidence") && job?.job_id && questions) {
      // tombstone 检查：已删记录不复写 IndexedDB
      const deletedIds = getDeletedJobIds();
      if (deletedIds.has(job.job_id)) return;
      const _wg = questions.filter(q => q.is_correct !== null && q.is_correct !== undefined).length;
      const _wsa = questions.filter(q => q.student_answer).length;
      addDiagEvent("questions_received", { source: "poll", jid: job.job_id.slice(0, 8), qcount: questions.length, with_grading: _wg, with_child_answer: _wsa });
      upsert({
        job_id: job.job_id,
        file_name: job.file_name || "",
        questions_count: questions.length,
        status,
        created_at: job.created_at || new Date().toISOString(),
        questions_snapshot: questions.map(q => ({
          question_id: q.question_id,
          question_number: q.question_number,
          question_text: (q.question_text || "").slice(0, 60),
          is_correct: q.is_correct ?? null,
          student_answer: q.student_answer ?? null,
        })) as QuestionSnapshot[],
      });
    }
  }, [status, questions]);

  const handleFile = useCallback((f: File | undefined) => {
    if (!f) return;
    setUploadError("");
    if (f.type.startsWith("image/")) {
      setPreview(URL.createObjectURL(f));
    } else {
      setPreview(null);
    }
    setFile(f);
    setPhase("selected");
    // 如果在 completed 视图触发 → 导航到 idle 显示上传 UI
    if (status === "completed" || status === "low_confidence") {
      router.push("/workspace");
    }
  }, [status, router]);

  // ── 两段式上传：init → navigate → upload → poll ──
  const startUpload = useCallback(async () => {
    if (!file) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      setUploadError("不支持的文件类型，请选择 JPG、PNG、WebP 或 PDF 文件");
      setPhase("error");
      return;
    }
    try {
      // ── 去重检测 ──
      let imageHash = "";
      if (file.type.startsWith("image/")) {
        try {
          const buf = await file.arrayBuffer();
          const hashBuf = await crypto.subtle.digest("SHA-256", buf);
          imageHash = Array.from(new Uint8Array(hashBuf))
            .map(b => b.toString(16).padStart(2, "0"))
            .join("");
          const dup = history.find(
            h => h.image_hash === imageHash && h.questions_count > 0
          );
          if (dup) {
            const ts = new Date(dup.created_at);
            const ago = Math.floor((Date.now() - ts.getTime()) / 3600000);
            const agoStr = ago < 1 ? "不到1小时前" : ago < 24 ? `${ago}小时前` : `${Math.floor(ago/24)}天前`;
            const ok = window.confirm(
              `⚠️ 检测到相同作业\n\n这张作业曾在 ${agoStr} 上传过（${dup.questions_count}题），是否重新识别？\n\n"取消"则不重复上传。`
            );
            if (!ok) { setPhase("idle"); return; }
          }
        } catch { /* hash 计算失败不阻塞 */ }
      }

      // ── 压缩 ──
      setPhase("compressing");
      let uploadFile: File;
      if (file.type.startsWith("image/")) {
        const result = await compressImage(file);
        setCompressInfo(
          `${(result.originalSize / 1024).toFixed(0)} KB → ${(result.compressedSize / 1024).toFixed(0)} KB`
        );
        uploadFile = result.file;
      } else {
        uploadFile = file;
      }

      // ── 第一步：init 创建任务 ──
      setPhase("initializing");
      const clientUploadId = uuidv4();
      addDiagEvent("init_start", { cu: clientUploadId.slice(0, 12), fsize: uploadFile.size });
      const tInit = Date.now();
      const initResp = await parseJobApi.initJob({
        client_upload_id: clientUploadId,
        file_name: file.name,
        file_size: uploadFile.size,
        mime_type: uploadFile.type,
      });
      if (!initResp.ok || !initResp.data?.job_id) {
        addDiagEvent("init_fail", { code: initResp.code, msg: initResp.message?.slice(0, 40), ms: Date.now() - tInit });
        throw new Error(initResp.message || "创建任务失败");
      }
      const jid = initResp.data.job_id;
      addDiagEvent("init_success", { jid: jid.slice(0, 8), status: initResp.data.status, ms: Date.now() - tInit });
      setUploadJobId(jid);
      upsert({
        job_id: jid, file_name: file.name, questions_count: 0,
        status: "uploaded", created_at: new Date().toISOString(),
        image_hash: imageHash || undefined,
      });

      // ── 导航到任务页，开始轮询 ──
      setIsRestoring(true);  // 触发统一"处理中"视图，消除 loading 白屏间隙
      const targetUrl = `/workspace?job_id=${jid}`;
      addDiagEvent("router_replace", { from: window.location.search || "/", to: targetUrl });
      router.replace(targetUrl);

      // ── 第二步：异步上传图片 ──
      setPhase("uploading");
      addDiagEvent("upload_start", { jid: jid.slice(0, 8), fsize: uploadFile.size });
      const tUpload = Date.now();
      const uploadResp = await parseJobApi.uploadFileToJob(jid, uploadFile);
      const uploadElapsed = Date.now() - tUpload;
      if (!uploadResp.ok) {
        addDiagEvent(uploadResp.code === "timeout" ? "upload_timeout" : "upload_fail", { jid: jid.slice(0,8), code: uploadResp.code, msg: uploadResp.message?.slice(0,40), ms: uploadElapsed });
        // 保留 job_id 不丢，写入 pending 触发 recover
        setPendingUpload(clientUploadId, file.name);
        setUploadError(uploadResp.message || "上传超时，后台仍在处理…");
        setPhase("recovering");  // 触发 recover useEffect
        return;
      }
      // 上传成功，轮询接管
      addDiagEvent("upload_success", { jid: jid.slice(0, 8), status: uploadResp.data?.status, ms: uploadElapsed });
      setPhase("idle");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "上传失败，请重试";
      setUploadError(msg);
      setPhase("error");
    }
  }, [file, router, upsert, history]);

  // ── 重试：优先恢复已有 job，再尝试重传 ──
  const handleRetry = useCallback(async () => {
    setUploadError("");
    // 1. 有 job_id 先尝试恢复（可能后端已完成）
    if (uploadJobId) {
      setPhase("recovering");
      try {
        const res = await parseJobApi.recoverByJobId(uploadJobId);
        if (res.ok && res.data) {
          const d = res.data;
          addDiagEvent("retry_recover_ok", { jid: d.job_id?.slice(0, 8) || "", status: d.status, qcount: d.questions_count });
          if (d.questions_count > 0) {
            addDiagEvent("questions_received", { source: "retry_recover", jid: d.job_id?.slice(0,8) || "", qcount: d.questions_count });
          }
          if (d.status === "completed" && d.questions_count > 0) {
            router.replace(`/workspace?job_id=${d.job_id}`);
            setPhase("idle");
            return;
          }
          // 后端已有记录但未完成 → 继续轮询
          if (d.status !== "failed") {
            router.replace(`/workspace?job_id=${d.job_id}`);
            setPhase("idle");
            return;
          }
        }
      } catch { /* recover fails → fall through to re-upload */ }
    }
    // 2. 没有 job_id 或恢复失败 → 重新上传
    if (!file || !uploadJobId) {
      startUpload();
      return;
    }
    try {
      setPhase("uploading");
      const resp = await parseJobApi.uploadFileToJob(uploadJobId, file);
      if (!resp.ok) {
        setUploadError(resp.message || "重试上传失败");
        setPhase("recovering");
        return;
      }
      setPhase("idle");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "重试上传失败";
      setUploadError(msg);
      setPhase("recovering");
    }
  }, [file, uploadJobId, startUpload, router]);

  const resetUpload = () => {
    setFile(null);
    setPreview(null);
    setPhase("idle");
    setUploadError("");
    setCompressInfo("");
  };

  const formatRelative = (iso: string): string => {
    const ts = new Date(iso).getTime();
    if (isNaN(ts)) return "未知时间";
    const diff = Date.now() - ts;
    if (diff < 0) return "刚刚";
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  };

  // 合并展示：本地历史 + API 补充（去重，API 补 q=0）→ tombstone 统一过滤
  const deletedIds = getDeletedJobIds();
  const localIds = new Set(history.map((h) => h.job_id));
  const apiOnly = apiRecent.filter((r) => !localIds.has(r.job_id) && !deletedIds.has(r.job_id));
  const completedHistory = history.filter((h) => (h.status === "completed" || h.status === "low_confidence" || h.status === "uploaded") && !deletedIds.has(h.job_id));
  const allHistory = [...completedHistory.map(h => {
    // 如果 localStorage 里 q=0，从 API 补（异步时序导致 effect 漏写）
    if (h.questions_count === 0) {
      const apiMatch = apiRecent.find(r => r.job_id === h.job_id);
      if (apiMatch && apiMatch.questions_count > 0) {
        return { ...h, questions_count: apiMatch.questions_count };
      }
    }
    return h;
  }), ...apiOnly.map(r => ({
    job_id: r.job_id,
    file_name: r.file_name || "",
    questions_count: r.questions_count || 0,
    status: r.status || "completed",
    created_at: r.created_at || "",
  }))].filter(h =>
    h.questions_count > 0
  ).sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  // ── 处理中（恢复 / 上传 / 轮询）── 合并为单实例，避免 ProcessingStatus 卸载重挂载
  const terminalSet = new Set(["completed", "failed", "low_confidence", "needs_review"]);
  if ((isRestoring || status === "loading" || status === "polling") && !terminalSet.has(status)) {
    return (
      <div className={`space-y-4 ${isRestoring ? 'pb-4' : ''}`}>
        {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}
        {!isRestoring && (
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">正在处理中…</span>
            <button
              onClick={() => { resetUpload(); router.push("/workspace"); }}
              className="text-xs text-primary border border-primary rounded-lg px-3 py-1"
            >
              新上传
            </button>
          </div>
        )}
        <ProcessingStatus status={job?.status || "uploaded"} />
      </div>
    );
  }

  // ── idle ──
  if (status === "idle") {
    // 自动触发相机（从 completed 视图点「拍下一张作业」进入）
    const actionParam = searchParams.get("action");
    if (actionParam === "camera") {
      setTimeout(() => cameraRef.current?.click(), 200);
    }
    return (
      <div className="space-y-6 pb-4">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{childName || "内测体验中"}</span>
          <span className="text-primary font-medium">剩余 50 学豆</span>
        </div>

        {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}

        {phase === "compressing" || phase === "initializing" || phase === "uploading" ? (
          <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-4">
            <Loader2 className="w-12 h-12 text-primary mx-auto animate-spin" strokeWidth={1.5} />
            <div>
              <p className="text-foreground font-medium">
                {phase === "compressing" ? "正在压缩图片…" : phase === "initializing" ? "任务已创建，正在上传图片…" : "正在上传…"}
              </p>
              {compressInfo && (
                <p className="text-muted-foreground text-sm mt-1">{compressInfo}</p>
              )}
            </div>
          </div>
        ) : phase === "recovering" ? (
          <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-4">
            <Loader2 className="w-12 h-12 text-primary mx-auto animate-spin" strokeWidth={1.5} />
            <div>
              <p className="text-foreground font-medium">上传已进入后台解析，正在自动找回结果…</p>
              <p className="text-muted-foreground text-sm mt-1">请勿关闭页面，预计 1-2 分钟</p>
            </div>
          </div>
        ) : phase === "error" ? (
          <ErrorDisplay message={uploadError} action="请检查文件后重试" onRetry={handleRetry} />
        ) : file ? (
          <div className="space-y-4">
            <div className="bg-card rounded-2xl p-4 shadow-sm border border-border space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  {file.type === "application/pdf" ? (
                    <FileText className="w-10 h-10 text-red-400 shrink-0" />
                  ) : preview ? (
                    <img src={preview} alt="preview" className="w-16 h-16 object-cover rounded-lg shrink-0" />
                  ) : (
                    <Image className="w-10 h-10 text-primary shrink-0" />
                  )}
                  <div className="min-w-0">
                    <p className="text-sm text-foreground truncate">{file.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {(file.size / 1024 / 1024).toFixed(1)} MB
                    </p>
                  </div>
                </div>
                <button onClick={resetUpload} className="text-muted-foreground hover:text-foreground">
                  <X className="w-5 h-5" />
                </button>
              </div>
              {file.type === "application/pdf" && (
                <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
                  当前将处理第 1 页，更多页选择将在后续版本开放
                </p>
              )}
            </div>
            <button
              onClick={startUpload}
              className="w-full rounded-xl bg-primary text-primary-foreground py-3.5 text-sm font-medium hover:opacity-90 transition shadow-sm"
            >
              开始识别
            </button>
          </div>
        ) : (
          <>
            <div
              className="bg-primary/5 border-2 border-dashed border-primary/30 rounded-2xl p-8 text-center space-y-3 hover:bg-primary/10 transition cursor-pointer"
              onClick={() => galleryRef.current?.click()}
            >
              <Upload className="w-10 h-10 text-primary mx-auto" strokeWidth={1.5} />
              <div>
                <p className="text-foreground font-semibold">拍整页作业 / 上传 PDF</p>
                <p className="text-muted-foreground text-sm mt-1">
                  上传后 AI 将自动识别题目、判断对错并给出解析
                </p>
              </div>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => cameraRef.current?.click()}
                className="flex-1 flex items-center justify-center gap-2 rounded-xl border border-border py-3 text-sm text-foreground hover:bg-muted transition"
              >
                <Camera className="w-4 h-4" /> 拍照
              </button>
              <button
                onClick={() => galleryRef.current?.click()}
                className="flex-1 flex items-center justify-center gap-2 rounded-xl border border-border py-3 text-sm text-foreground hover:bg-muted transition"
              >
                <Image className="w-4 h-4" /> 从相册选择
              </button>
            </div>
            <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => handleFile(e.target.files?.[0])} />
            <input ref={galleryRef} type="file" accept="image/*,.pdf" className="hidden" onChange={(e) => handleFile(e.target.files?.[0])} />
          </>
        )}

        {/* 历史记录 — 空历史不展示 */}
        {allHistory.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-foreground">历史记录（最近 7 天）</h2>
            <span className="text-xs text-muted-foreground">← 左划可删除</span>
          </div>

          <div className="space-y-2">
            {allHistory.map((h) => (
              <SwipeableCard
                key={h.job_id}
                onTap={() => router.push(`/workspace?job_id=${h.job_id}`)}
                actions={[
                  { label: "取消", color: "blue", onClick: () => {} },
                  { label: "删除", color: "red", onClick: () => handleDelete(h.job_id) },
                ]}
              >
                <div className="flex items-center gap-3">
                  <Clock className="w-5 h-5 text-muted-foreground shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-foreground truncate">{h.file_name || "作业记录"}</p>
                    <p className="text-xs text-muted-foreground">
                      {formatRelative(h.created_at)} · {h.questions_count || "?"} 题
                    </p>
                  </div>
                  <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0" />
                </div>
              </SwipeableCard>
            ))}
          </div>
        </section>
        )}
      </div>
    );
  }

  // ── failed ──
  if (status === "failed") {
    // 清理导致失败的 stale job_id，防止重试后再次跳回失败态
    const jid = searchParams.get("job_id");
    return (
      <div className="space-y-4 pb-4">
        {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}
        <ErrorDisplay
          message={error || "该任务已失效"}
          action="返回工作台重新上传"
          onRetry={() => {
            if (jid && history.some(h => h.job_id === jid)) {
              removeEntry(jid);
            }
            router.push("/workspace");
            resetUpload();
          }}
        />
      </div>
    );
  }

  // ── needs_review ──
  if (status === "needs_review") {
    const jid = searchParams.get("job_id");
    const hint = getRecognitionHint(status, documentClassification, 0);
    return (
      <div className="space-y-4 pb-4">
        {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}
        <div className="bg-card rounded-2xl p-8 shadow-sm border border-border text-center space-y-4">
          <div className="flex justify-center">
            <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-amber-500" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          </div>
          <h2 className="text-lg font-semibold text-foreground">{hint.title}</h2>
          <p className="text-sm text-muted-foreground">
            {documentClassification ? hint.description : (error || hint.description)}
          </p>
          <RecognitionHintCard status={status} classification={documentClassification} questionCount={0} />
          <div className="flex gap-3 justify-center pt-2">
            <button
              onClick={() => {
                if (jid && history.some(h => h.job_id === jid)) removeEntry(jid);
                router.push("/workspace");
                resetUpload();
              }}
              className="rounded-xl border border-border px-6 py-2.5 text-sm text-foreground hover:bg-muted transition"
            >
              返回工作台
            </button>
            <button
              onClick={() => {
                router.push("/workspace?action=camera");
                resetUpload();
              }}
              className="rounded-xl bg-primary text-primary-foreground px-6 py-2.5 text-sm font-medium hover:opacity-90 transition"
            >
              重拍一张
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── completed / low_confidence ──
  const qs = questions || [];
  const groupSize = calcGroupSize(qs.length);
  const isRenderableBbox = (bbox?: number[] | null): bbox is [number, number, number, number] => {
    if (!bbox || bbox.length !== 4) return false;
    const [x, y, w, h] = bbox;
    return [x, y, w, h].every((value) => Number.isFinite(value)) && w > 0 && h > 0;
  };
  const overlayImageUrl = job?.image_url || qs.find((q) => q.image_url)?.image_url || undefined;

  // 诊断：渲染前检查 grading 字段
  const _render_wg = qs.filter(q => q.is_correct !== null && q.is_correct !== undefined).length;
  const _render_wsa = qs.filter(q => q.student_answer).length;

  if (qs.length === 0) {
    const jid = searchParams.get("job_id");
    const hint = getRecognitionHint(status, documentClassification, 0);
    return (
      <div className="space-y-4 pb-4">
        {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}
        <div className="bg-card rounded-2xl p-8 shadow-sm border border-border">
          <EmptyState
            title={hint.title}
            description={hint.description}
            action={
              <div className="flex gap-3 pt-2">
                <button
                  onClick={() => {
                    if (jid && history.some(h => h.job_id === jid)) removeEntry(jid);
                    router.push("/workspace");
                    resetUpload();
                  }}
                  className="rounded-xl border border-border px-5 py-2 text-sm text-foreground hover:bg-muted transition"
                >
                  返回工作台
                </button>
                <button
                  onClick={() => {
                    router.push("/workspace?action=camera");
                    resetUpload();
                  }}
                  className="rounded-xl bg-primary text-primary-foreground px-5 py-2 text-sm font-medium hover:opacity-90 transition"
                >
                  重拍一张
                </button>
              </div>
            }
          />
          <div className="mt-4">
            <RecognitionHintCard status={status} classification={documentClassification} questionCount={0} />
          </div>
        </div>
      </div>
    );
  }

  // 按 section_title 预分组（如果有分组信息）
  const hasSections = qs.some(q => q.section_title);
  let sectionedGroups: { title: string; questions: Question[]; startNumber: number }[] = [];
  if (hasSections) {
    const sections = new Map<string, Question[]>();
    for (const q of qs) {
      const key = q.section_title || 'default';
      if (!sections.has(key)) sections.set(key, []);
      sections.get(key)!.push(q);
    }
    let num = 1;
    for (const [title, sqs] of sections) {
      sectionedGroups.push({ title: title === 'default' ? '' : title, questions: sqs, startNumber: num });
      num += sqs.length;
    }
  }

  const groups = hasSections
    ? []  // 按 section 内部再分组
    : groupQuestions(qs, groupSize);

  if (hasSections) {
    for (const sec of sectionedGroups) {
      const secQs = sec.questions;
      const secGs = groupQuestions(secQs, calcGroupSize(secQs.length));
      for (let gi = 0; gi < secGs.length; gi++) {
        groups.push(secGs[gi]);
      }
    }
  }

  const handleCameraFromCompleted = () => {
    // 方案 1：直接触发相机（如果 input 在 DOM 中）
    if (cameraRef.current) {
      cameraRef.current.click();
      return;
    }
    // 方案 2：回退到导航方式
    router.push("/workspace?action=camera");
  };

  return (
    <div className="space-y-4 pb-4">
      {showDebug && <DiagPanel events={diagEvents} expanded={diagExpanded} onToggle={() => setDiagExpanded(!diagExpanded)} />}
      {status === "low_confidence" && (
        <RecognitionHintCard status={status} classification={documentClassification} compact questionCount={qs.length} />
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          共 {job?.questions_count || qs.length} 题
          <span className="text-xs text-muted-foreground ml-2 font-normal">
            (判对错:{_render_wg}/{qs.length} 答案:{_render_wsa}/{qs.length})
          </span>
        </h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { resetUpload(); router.push("/workspace"); }}
            className="text-xs text-primary border border-primary rounded-lg px-3 py-1"
          >
            新上传
          </button>
          <span className="text-xs text-muted-foreground">
            点击题目组展开查看
          </span>
        </div>
      </div>

      <GradingOverlay marks={overlay || []} groups={group_boxes || []} imageUrl={overlayImageUrl} />

      <div className="space-y-3">
        {groups.map((g, gi) => {
          let sectionLabel = "";
          if (hasSections && g.length > 0) {
            const firstQ = g[0];
            if (firstQ.section_title) {
              const prevFirstQ = gi > 0 ? groups[gi - 1]?.[0] : null;
              if (!prevFirstQ || prevFirstQ.section_title !== firstQ.section_title) {
                sectionLabel = firstQ.section_title;
              }
            }
          }
          return (
            <div key={gi}>
              {sectionLabel && (
                <h3 className="text-sm font-semibold text-foreground mb-2 mt-4 first:mt-0">{sectionLabel}</h3>
              )}
              <QuestionGroup
                groupIndex={gi}
                startNumber={gi * groupSize + 1}
                endNumber={gi * groupSize + g.length}
                questions={g}
                defaultOpen={gi === 0}
              />
            </div>
          );
        })}
      </div>

      {/* 拍下一张作业 — 直接触发相机 */}
      <button
        onClick={handleCameraFromCompleted}
        className="w-full mt-4 rounded-xl bg-primary text-primary-foreground py-3.5 text-sm font-medium hover:opacity-90 transition shadow-sm flex items-center justify-center gap-2"
      >
        <Camera className="w-4 h-4" />
        拍下一张作业
      </button>

      {/* 隐藏的文件输入 — 始终渲染在 completed 视图 */}
      <input ref={cameraRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => handleFile(e.target.files?.[0])} />
      <input ref={galleryRef} type="file" accept="image/*,.pdf" className="hidden" onChange={(e) => handleFile(e.target.files?.[0])} />
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <WorkspaceContent />
    </Suspense>
  );
}
