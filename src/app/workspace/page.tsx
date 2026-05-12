"use client";

import { Suspense, useState, useRef, useCallback, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Upload, ArrowRight, Clock, Loader2, X, Image, Camera, FileText } from "lucide-react";
import ProcessingStatus from "@/components/processing/ProcessingStatus";
import BboxOverlay from "@/components/question-list/BboxOverlay";
import QuestionGroup, { calcGroupSize, groupQuestions } from "@/components/question-list/QuestionGroup";
import { useParseJobPolling } from "@/hooks/useParseJobPolling";
import { useJobHistory } from "@/hooks/useJobHistory";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import SwipeableCard from "@/components/common/SwipeableCard";
import { compressImage } from "@/lib/imageCompress";
import { uuidv4 } from "@/lib/uuid";
import { parseJobApi } from "@/lib/api";
import type { Bbox } from "@/components/question-list/BboxOverlay";
import type { RecentJob } from "@/lib/types";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

type UploadPhase = "idle" | "selected" | "compressing" | "uploading" | "error";

function WorkspaceContent() {
  const { job, questions, status, error } = useParseJobPolling();
  const searchParams = useSearchParams();
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

  // ── 7 天滚动历史缓存 ──
  const { history, upsert, removeEntry, clearAll } = useJobHistory();

  // ── API 补充（localStorage 清除/损坏后的恢复）──
  const [apiRecent, setApiRecent] = useState<RecentJob[]>([]);

  // ── 已隐藏的 API 记录（左划删除后不再显示）──
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(new Set());

  // ── 恢复中（防止闪烁）──
  const [isRestoring, setIsRestoring] = useState(false);

  // 页面挂载时从后端拉取历史记录（补充 localStorage 可能丢失的数据）
  useEffect(() => {
    parseJobApi.getRecent().then((res) => {
      if (res.ok && res.data) setApiRecent(res.data);
    }).catch(() => {});
  }, []);

  // 删除处理：localStorage 条目走 removeEntry，API 条目走 hiddenIds
  const handleDelete = (jobId: string) => {
    if (history.some(h => h.job_id === jobId)) {
      removeEntry(jobId);
    } else {
      setHiddenIds(prev => new Set(prev).add(jobId));
    }
  };



  useEffect(() => {
    if (status === "polling" || status === "failed" || status === "completed") {
      setIsRestoring(false);
    }
  }, [status]);

  // 页面 idle：尝试恢复上次任务
  useEffect(() => {
    if (status !== "idle") return;
    if (history.length > 0) {
      const last = history[0];
      if (last.status !== "completed" && last.status !== "failed") {
        const age = Date.now() - new Date(last.created_at).getTime();
        if (age < 24 * 60 * 60 * 1000) {
          setIsRestoring(true);
          router.replace(`/workspace?job_id=${last.job_id}`);
          return;
        }
      }
    }

  }, [status, router, history]);

  // 任务完成 → 更新历史
  useEffect(() => {
    if (status === "completed" && job?.job_id && questions) {
      upsert({
        job_id: job.job_id,
        file_name: job.file_name || "",
        questions_count: questions.length,
        status: "completed",
        created_at: job.created_at || new Date().toISOString(),
      });
    }
  }, [status]);

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
      upsert({
        job_id: resp.data.job_id,
        file_name: file.name,
        questions_count: 0,
        status: "uploaded",
        created_at: new Date().toISOString(),
      });
      router.push(`/workspace?job_id=${resp.data.job_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "上传失败，请重试";
      setUploadError(msg);
      setPhase("error");
    }
  }, [file, router, upsert]);

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

  // 合并展示：本地历史 + API 补充（去重）
  const localIds = new Set(history.map((h) => h.job_id));
  const apiOnly = apiRecent.filter((r) => !localIds.has(r.job_id));
  const completedHistory = history.filter((h) => h.status === "completed" || h.status === "uploaded");
  const allHistory = [...completedHistory, ...apiOnly.map(r => ({
    job_id: r.job_id,
    file_name: r.file_name || "",
    questions_count: r.questions_count || 0,
    status: r.status || "completed",
    created_at: r.created_at || "",
  }))].filter(h =>
    !hiddenIds.has(h.job_id) &&
    h.questions_count > 0 &&
    h.file_name &&
    h.created_at
  );

  // ── 恢复中 ──
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

  // ── idle ──
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
                    <p className="text-sm text-foreground truncate">{h.file_name}</p>
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
    return (
      <ErrorDisplay
        message={error}
        action="请重新上传作业"
        onRetry={() => {
          const jid = searchParams.get("job_id");
          if (jid) {
            upsert({
              job_id: jid,
              file_name: job?.file_name || "",
              questions_count: 0,
              status: "failed",
              created_at: job?.created_at || new Date().toISOString(),
            });
          }
          router.push("/workspace");
          resetUpload();
        }}
      />
    );
  }

  // ── polling ──
  if (status === "polling") {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">正在处理中…</span>
          <button
            onClick={() => { resetUpload(); router.push("/workspace"); }}
            className="text-xs text-primary border border-primary rounded-lg px-3 py-1"
          >
            新上传
          </button>
        </div>
        <ProcessingStatus status={job?.status || "uploaded"} />
      </div>
    );
  }

  // ── completed ──
  const qs = questions || [];
  const groupSize = calcGroupSize(qs.length);
  const groups = groupQuestions(qs, groupSize);

  return (
    <div className="space-y-4 pb-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">
          共 {job?.questions_count || qs.length} 题
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

      <BboxOverlay bboxes={qs.filter((q) => q.bbox && q.bbox.length === 4).map((q) => ({
        question_id: q.question_id,
        bbox: q.bbox as [number, number, number, number],
        question_number: q.question_number,
      }))} activeIndex={activeIndex} imageUrl={undefined} />

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

      {/* 拍下一张入口 */}
      <button
        onClick={() => { resetUpload(); router.push("/workspace"); }}
        className="w-full mt-4 rounded-xl bg-primary text-primary-foreground py-3.5 text-sm font-medium hover:opacity-90 transition shadow-sm flex items-center justify-center gap-2"
      >
        <Camera className="w-4 h-4" />
        拍下一张作业
      </button>
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
