"use client";

import { Suspense, useState, useRef, useCallback, useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Upload, ArrowRight, Clock, Loader2, X, Image, Camera, FileText, CheckCircle2 } from "lucide-react";
import ProcessingStatus from "@/components/processing/ProcessingStatus";
import BboxOverlay from "@/components/question-list/BboxOverlay";
import QuestionGroup, { calcGroupSize, groupQuestions } from "@/components/question-list/QuestionGroup";
import { useParseJobPolling } from "@/hooks/useParseJobPolling";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import { compressImage } from "@/lib/imageCompress";
import { uuidv4 } from "@/lib/uuid";
import { parseJobApi } from "@/lib/api";
import type { Bbox } from "@/components/question-list/BboxOverlay";
import type { RecentJob } from "@/lib/types";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const LS_LAST_JOB = "yomi_last_job";
const LS_RECENT_TTL = 24 * 60 * 60 * 1000;

type UploadPhase = "idle" | "selected" | "compressing" | "uploading" | "error";

function WorkspaceContent() {
  const { job, questions, status, error } = useParseJobPolling();
  const [activeIndex, setActiveIndex] = useState(-1);
  const router = useRouter();

  // ── 拍照上传状态 ──
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [uploadError, setUploadError] = useState("");
  const [compressInfo, setCompressInfo] = useState("");
  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  // ── 最近解析（真实 API）──
  const [recentJobs, setRecentJobs] = useState<RecentJob[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);

  // ── 恢复中（防止闪烁）──
  const [isRestoring, setIsRestoring] = useState(false);

  // 当进入 polling / failed / completed 时解除恢复状态
  useEffect(() => {
    if (status === "polling" || status === "failed" || status === "completed") {
      setIsRestoring(false);
    }
  }, [status]);

  // 页面加载时：恢复上次未完成的任务 + 拉取最近列表
  useEffect(() => {
    if (status !== "idle") return;
    try {
      const saved = localStorage.getItem(LS_LAST_JOB);
      if (saved) {
        const { job_id, ts } = JSON.parse(saved);
        if (Date.now() - ts < LS_RECENT_TTL) {
          setIsRestoring(true);
          router.replace(`/workspace?job_id=${job_id}`);
          return;
        }
      }
    } catch { /* ignore */ }
    // 拉取最近解析列表
    setRecentLoading(true);
    parseJobApi.getRecent().then((resp) => {
      if (resp.ok && resp.data) setRecentJobs(resp.data);
      setRecentLoading(false);
    }).catch(() => setRecentLoading(false));
  }, [status, router]);

  // 上传成功：保存 job_id 到 localStorage
  const saveLastJob = useCallback((jobId: string) => {
    try {
      localStorage.setItem(LS_LAST_JOB, JSON.stringify({ job_id: jobId, ts: Date.now() }));
    } catch { /* ignore */ }
  }, []);

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
  }, []);

  const startUpload = useCallback(async () => {
    if (!file) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      setUploadError("不支持的文件类型，请选择 JPG、PNG、WebP 或 PDF 文件");
      setPhase("error");
      return;
    }
    try {
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
      setPhase("uploading");
      const clientTaskId = uuidv4();
      const resp = await parseJobApi.create(uploadFile, clientTaskId);
      if (!resp.ok || !resp.data?.job_id) {
        throw new Error(resp.message || "服务器未返回任务 ID");
      }
      saveLastJob(resp.data.job_id);
      router.push(`/workspace?job_id=${resp.data.job_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "上传失败，请重试";
      setUploadError(msg);
      setPhase("error");
    }
  }, [file, router, saveLastJob]);

  const resetUpload = () => {
    setFile(null);
    setPreview(null);
    setPhase("idle");
    setUploadError("");
    setCompressInfo("");
  };

  const formatRelative = (iso: string): string => {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins} 分钟前`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  };

  // ── 恢复中：显示进度占位 ──
  if (isRestoring && (status === "loading" || status === "polling")) {
    return <ProcessingStatus status={job?.status || "uploaded"} />;
  }

  // ── loading ──
  if (status === "loading") {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
      </div>
    );
  }

  // ── idle: 上传入口 ──
  if (status === "idle") {
    return (
      <div className="space-y-6 pb-4">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">内测体验中</span>
          <span className="text-primary font-medium">剩余 50 学豆</span>
        </div>

        {phase === "compressing" || phase === "uploading" ? (
          <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-4">
            <Loader2 className="w-12 h-12 text-primary mx-auto animate-spin" strokeWidth={1.5} />
            <div>
              <p className="text-foreground font-medium">
                {phase === "compressing" ? "正在压缩图片…" : "正在上传…"}
              </p>
              {compressInfo && (
                <p className="text-muted-foreground text-sm mt-1">{compressInfo}</p>
              )}
            </div>
          </div>
        ) : phase === "error" ? (
          <ErrorDisplay message={uploadError} action="请检查文件后重试" onRetry={startUpload} />
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

        <section>
          <h2 className="text-sm font-semibold text-foreground mb-3">最近解析</h2>
          {recentLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-5 h-5 text-muted-foreground animate-spin" />
            </div>
          ) : recentJobs.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">暂无解析记录</p>
          ) : (
            recentJobs.map((r) => (
              <Link
                key={r.job_id}
                href={`/workspace?job_id=${r.job_id}`}
                onClick={() => saveLastJob(r.job_id)}
                className="block bg-card rounded-xl p-4 shadow-sm border border-border flex items-center gap-3 hover:shadow-md transition mb-2"
              >
                <Clock className="w-5 h-5 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground truncate">{r.file_name}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatRelative(r.created_at)} · {r.questions_count || "?"} 题
                    {r.status !== "completed" ? ` · ${r.status}` : ""}
                  </p>
                </div>
                <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0" />
              </Link>
            ))
          )}
        </section>
      </div>
    );
  }

  // ── failed ──
  if (status === "failed") {
    setIsRestoring(false);
    return (
      <ErrorDisplay
        message={error}
        action="请重新上传作业"
        onRetry={() => {
          router.push("/workspace");
          resetUpload();
          try { localStorage.removeItem(LS_LAST_JOB); } catch {}
        }}
      />
    );
  }

  // ── polling ──
  if (status === "polling") {
    setIsRestoring(false);
    return <ProcessingStatus status={job?.status || "uploaded"} />;
  }

  // ── completed ──
  setIsRestoring(false);
  const qs = questions || [];
  const groupSize = calcGroupSize(qs.length);
  const groups = groupQuestions(qs, groupSize);

  const bboxes: Bbox[] = qs
    .filter((q) => q.bbox && q.bbox.length === 4)
    .map((q) => ({
      question_id: q.question_id,
      bbox: q.bbox as [number, number, number, number],
      question_number: q.question_number,
    }));

  return (
    <div className="space-y-4 pb-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          共 {job?.questions_count || qs.length} 题
        </h2>
        <span className="text-xs text-muted-foreground">
          点击题目组展开查看
        </span>
      </div>

      <BboxOverlay
        bboxes={bboxes}
        activeIndex={activeIndex}
        imageUrl={undefined}
      />

      {/* 分组题目 */}
      <div className="space-y-3">
        {groups.map((g, gi) => {
          const start = gi * groupSize + 1;
          const end = start + g.length - 1;
          return (
            <QuestionGroup
              key={gi}
              groupIndex={gi}
              startNumber={start}
              endNumber={end}
              questions={g}
              defaultOpen={gi === 0}
            />
          );
        })}
      </div>
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
