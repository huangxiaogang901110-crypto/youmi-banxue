"use client";

import { Suspense, useState, useRef, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Upload, ArrowRight, Clock, Loader2, X, Image, Camera, FileText, CheckCircle2 } from "lucide-react";
import ProcessingStatus from "@/components/processing/ProcessingStatus";
import BboxOverlay from "@/components/question-list/BboxOverlay";
import { useParseJobPolling } from "@/hooks/useParseJobPolling";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import { compressImage } from "@/lib/imageCompress";
import { uuidv4 } from "@/lib/uuid";
import { parseJobApi } from "@/lib/api";
import type { Bbox } from "@/components/question-list/BboxOverlay";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

const mockRecent = [
  { id: "job-1", name: "三年级数学练习册 p23", time: "10 分钟前", count: 5 },
];

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
      router.push(`/workspace?job_id=${resp.data.job_id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "上传失败，请重试";
      setUploadError(msg);
      setPhase("error");
    }
  }, [file, router]);

  const resetUpload = () => {
    setFile(null);
    setPreview(null);
    setPhase("idle");
    setUploadError("");
    setCompressInfo("");
  };

  // ── idle: 上传入口 ──
  if (status === "idle") {
    return (
      <div className="space-y-6 pb-4">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">内测体验中</span>
          <span className="text-primary font-medium">剩余 50 学豆</span>
        </div>

        {/* 上传成功 */}
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
          /* 已选文件，确认上传 */
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
          /* 默认：上传入口 */
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
          {mockRecent.map((r) => (
            <div key={r.id} className="bg-card rounded-xl p-4 shadow-sm border border-border flex items-center gap-3">
              <Clock className="w-5 h-5 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-foreground truncate">{r.name}</p>
                <p className="text-xs text-muted-foreground">{r.time} · {r.count} 题</p>
              </div>
            </div>
          ))}
        </section>
      </div>
    );
  }

  // ── loading ──
  if (status === "loading") {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 text-primary animate-spin" />
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
          router.push("/workspace");
          resetUpload();
        }}
      />
    );
  }

  // ── polling ──
  if (status === "polling") {
    return <ProcessingStatus status={job?.status || "uploaded"} />;
  }

  // ── completed ──
  const bboxes: Bbox[] = (questions || [])
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
          共 {job?.questions_count || questions?.length || 0} 题
        </h2>
        <span className="text-xs text-muted-foreground">
          点击题目查看 AI 辅导
        </span>
      </div>

      <BboxOverlay
        bboxes={bboxes}
        activeIndex={activeIndex}
        imageUrl={undefined}
      />

      <div className="space-y-2">
        {(questions || []).map((q, i) => (
          <Link
            key={q.question_id}
            href={`/question?qid=${q.question_id}`}
            onClick={() => setActiveIndex(i)}
            className={`block bg-card rounded-xl p-4 shadow-sm border transition ${
              i === activeIndex ? "border-primary ring-1 ring-primary/20" : "border-border hover:shadow-md"
            }`}
          >
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground w-6 shrink-0">
                #{q.question_number}
              </span>
              <span className="flex-1 text-sm text-foreground line-clamp-2">
                {q.question_text}
              </span>
              <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0" />
            </div>
          </Link>
        ))}
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
