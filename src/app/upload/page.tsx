"use client";

import { useState, useRef, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Camera, Image, FileText, X, Loader2, CheckCircle2, ClipboardList, Send } from "lucide-react";
import { compressImage } from "@/lib/imageCompress";
import { uuidv4 } from "@/lib/uuid";
import { parseJobApi, homeworkApi } from "@/lib/api";
import type { HomeworkSubject } from "@/lib/types";
import ErrorDisplay from "@/components/common/ErrorDisplay";
import HomeworkList from "@/components/homework/HomeworkList";

type UploadStatus = "idle" | "selected" | "compressing" | "uploading" | "success" | "error";
type TabMode = "text" | "photo";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const STORAGE_KEY = "yomi_homework_subjects";
const STORAGE_DONE_KEY = "yomi_homework_done";

function UploadContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<TabMode>(
    (searchParams.get("tab") as TabMode) || "text"
  );

  // ── 拍照上传状态 ──
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [status, setStatus] = useState<UploadStatus>("idle");
  const [error, setError] = useState("");
  const [compressInfo, setCompressInfo] = useState("");
  const [jobId, setJobId] = useState("");

  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);

  // ── 贴文本状态 ──
  const [text, setText] = useState("");
  const [subjects, setSubjects] = useState<HomeworkSubject[]>([]);
  const [doneMap, setDoneMap] = useState<Record<string, boolean>>({});
  const [parseLoading, setParseLoading] = useState(false);
  const [parseError, setParseError] = useState("");
  const [showPreview, setShowPreview] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) setSubjects(JSON.parse(saved));
      const done = localStorage.getItem(STORAGE_DONE_KEY);
      if (done) setDoneMap(JSON.parse(done));
    } catch {}
  }, []);

  const persistSubjects = (s: HomeworkSubject[], d?: Record<string, boolean>) => {
    setSubjects(s);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    if (d !== undefined) {
      setDoneMap(d);
      localStorage.setItem(STORAGE_DONE_KEY, JSON.stringify(d));
    }
  };

  // ── 拍照逻辑 ──
  const handleFile = (f: File | undefined) => {
    if (!f) return;
    setError("");
    if (f.type.startsWith("image/")) {
      setPreview(URL.createObjectURL(f));
    } else {
      setPreview(null);
    }
    setFile(f);
    setStatus("selected");
  };

  const startUpload = async () => {
    if (!file) return;
    if (!ALLOWED_TYPES.includes(file.type)) {
      setError("不支持的文件类型，请选择 JPG、PNG、WebP 或 PDF 文件");
      setStatus("error");
      return;
    }
    try {
      setStatus("compressing");
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
      setStatus("uploading");
      const clientTaskId = uuidv4();
      const resp = await parseJobApi.create(uploadFile, clientTaskId);
      if (!resp.ok || !resp.data?.job_id) {
        throw new Error(resp.message || "服务器未返回任务 ID");
      }
      setJobId(resp.data.job_id);
      setStatus("success");
      setTimeout(() => {
        router.push(`/workspace?job_id=${resp.data!.job_id}`);
      }, 1000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "上传失败，请重试";
      setError(msg);
      setStatus("error");
    }
  };

  const resetAndRetry = () => {
    setError("");
    setStatus("selected");
    startUpload();
  };

  // ── 贴文本逻辑 ──
  const handleParse = async () => {
    if (!text.trim()) return;
    setParseLoading(true);
    setParseError("");
    try {
      const resp = await homeworkApi.parse(text.trim());
      if (!resp.ok || !resp.data) {
        setParseError((resp as any).message || "解析失败，请重试");
        return;
      }
      const incoming = resp.data.subjects;
      if (subjects.length === 0) {
        persistSubjects(incoming, {});
        return;
      }
      (window as any).__yomi_incoming = incoming;
      setShowPreview(true);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setParseLoading(false);
    }
  };

  const handleMergeConfirm = () => {
    const incoming: HomeworkSubject[] = (window as any).__yomi_incoming || [];
    const merged = mergeSubjects(subjects, incoming);
    persistSubjects(merged, doneMap);
    setShowPreview(false);
    delete (window as any).__yomi_incoming;
  };

  const handleMergeCancel = () => {
    setShowPreview(false);
    delete (window as any).__yomi_incoming;
  };

  const toggleTask = (si: number, ti: number) => {
    const key = `${si}-${ti}`;
    const next = { ...doneMap, [key]: !doneMap[key] };
    persistSubjects(subjects, next);
  };

  return (
    <div className="max-w-md mx-auto space-y-6 pb-4">
      {/* ── 顶部 Tab 切换 ── */}
      <div className="flex rounded-xl bg-muted p-1">
        <button
          onClick={() => setTab("text")}
          className={`flex-1 flex items-center justify-center gap-1.5 rounded-lg py-2.5 text-sm font-medium transition ${
            tab === "text"
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground"
          }`}
        >
          <ClipboardList className="w-4 h-4" />
          贴文本
        </button>
        <button
          onClick={() => setTab("photo")}
          className={`flex-1 flex items-center justify-center gap-1.5 rounded-lg py-2.5 text-sm font-medium transition ${
            tab === "photo"
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground"
          }`}
        >
          <Camera className="w-4 h-4" />
          拍照片
        </button>
      </div>

      {/* ── 贴文本面板 ── */}
      {tab === "text" && (
        <div className="space-y-4">
          {subjects.length > 0 && (
            <HomeworkList subjects={subjects} doneMap={doneMap} onToggle={toggleTask} />
          )}

          {showPreview && (
            <MergePreview
              existing={subjects}
              incoming={(window as any).__yomi_incoming || []}
              onConfirm={handleMergeConfirm}
              onCancel={handleMergeCancel}
            />
          )}

          <div className="space-y-3">
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <ClipboardList className="w-4 h-4" />
              复制微信群作业文本，粘贴到下方
            </p>
            <textarea
              className="w-full min-h-[120px] rounded-xl border border-border bg-card p-4 text-sm text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder={`示例格式：
语文：背诵第12课，完成练习册P15
数学：口算20题，订正错题
英语：听读Unit 3，抄写单词`}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <button
              onClick={handleParse}
              disabled={!text.trim() || parseLoading}
              className="w-full rounded-xl bg-primary text-primary-foreground py-3.5 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
            >
              {parseLoading ? (
                "解析中…"
              ) : (
                <span className="flex items-center justify-center gap-2">
                  <Send className="w-4 h-4" /> 生成作业清单
                </span>
              )}
            </button>
            {parseError && <ErrorDisplay message={parseError} onRetry={handleParse} />}
          </div>

          {subjects.length === 0 && !parseLoading && (
            <div className="bg-muted rounded-xl p-4 text-xs text-muted-foreground space-y-1">
              <p className="font-medium text-foreground text-sm mb-2">💡 支持格式</p>
              <p>• 科目：任务1，任务2</p>
              <p>• 科目 - 任务1 - 任务2</p>
              <p>• 【科目】任务1 / 任务2</p>
            </div>
          )}
        </div>
      )}

      {/* ── 拍照片面板 ── */}
      {tab === "photo" && (
        <div className="space-y-6">
          {status === "success" && (
            <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-4 animate-in fade-in">
              <CheckCircle2 className="w-16 h-16 text-primary mx-auto" strokeWidth={1.5} />
              <div>
                <p className="text-foreground font-semibold text-lg">上传成功</p>
                <p className="text-muted-foreground text-sm mt-1">正在跳转至工作台…</p>
              </div>
            </div>
          )}

          {(status === "compressing" || status === "uploading") && (
            <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-4">
              <Loader2 className="w-12 h-12 text-primary mx-auto animate-spin" strokeWidth={1.5} />
              <div>
                <p className="text-foreground font-medium">
                  {status === "compressing" ? "正在压缩图片…" : "正在上传…"}
                </p>
                {compressInfo && (
                  <p className="text-muted-foreground text-sm mt-1">{compressInfo}</p>
                )}
              </div>
            </div>
          )}

          {status === "error" && (
            <ErrorDisplay message={error} action="请检查文件后重试" onRetry={resetAndRetry} />
          )}

          {status !== "success" && status !== "compressing" && status !== "uploading" && (
            <>
              {!file ? (
                <div
                  className="border-2 border-dashed border-primary/30 rounded-2xl p-10 text-center space-y-4 bg-primary/5 cursor-pointer hover:bg-primary/10 transition"
                  onClick={() => galleryRef.current?.click()}
                >
                  <Image className="w-12 h-12 text-primary mx-auto" strokeWidth={1.5} />
                  <div>
                    <p className="text-foreground font-medium">点击拍照或选择图片</p>
                    <p className="text-muted-foreground text-sm mt-1">支持 JPG / PNG / PDF，单文件 ≤10MB</p>
                  </div>
                </div>
              ) : (
                <div className="bg-card rounded-2xl p-4 shadow-sm border border-border space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                      {file.type === "application/pdf" ? (
                        <FileText className="w-10 h-10 text-red-400 shrink-0" />
                      ) : preview ? (
                        <img src={preview} alt="preview" className="w-16 h-16 object-cover rounded-lg shrink-0" />
                      ) : null}
                      <div className="min-w-0">
                        <p className="text-sm text-foreground truncate">{file.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {(file.size / 1024 / 1024).toFixed(1)} MB
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => { setFile(null); setStatus("idle"); setError(""); }}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="w-5 h-5" />
                    </button>
                  </div>
                  {file.type === "application/pdf" && (
                    <p className="text-xs text-amber-600 bg-amber-50 rounded-lg px-3 py-2">
                      当前将处理第 1 页，更多页选择将在后续版本开放
                    </p>
                  )}
                </div>
              )}

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

              {file && (
                <button onClick={startUpload} className="w-full rounded-xl bg-primary text-primary-foreground py-3.5 text-sm font-medium hover:opacity-90 transition shadow-sm">
                  开始解析
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** 精确文本匹配去重合并 */
function mergeSubjects(
  existing: HomeworkSubject[],
  incoming: HomeworkSubject[]
): HomeworkSubject[] {
  const allTasks = new Set(
    existing.flatMap((s) => s.tasks.map((t) => t.trim()))
  );
  const result: HomeworkSubject[] = existing.map((s) => ({ ...s, tasks: [...s.tasks] }));

  for (const inc of incoming) {
    const newTasks = inc.tasks.filter((t) => !allTasks.has(t.trim()));
    if (newTasks.length === 0) continue;
    const existingSubj = result.find((s) => s.name === inc.name);
    if (existingSubj) {
      existingSubj.tasks.push(...newTasks);
    } else {
      result.push({ name: inc.name, tasks: newTasks });
    }
    newTasks.forEach((t) => allTasks.add(t.trim()));
  }
  return result;
}

/** 合并预览弹窗 */
function MergePreview({
  existing,
  incoming,
  onConfirm,
  onCancel,
}: {
  existing: HomeworkSubject[];
  incoming: HomeworkSubject[];
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const existingSet = new Set(
    existing.flatMap((s) => s.tasks.map((t) => t.trim()))
  );
  const skipped = incoming.flatMap((s) =>
    s.tasks.filter((t) => existingSet.has(t.trim()))
  );
  const added = incoming.flatMap((s) =>
    s.tasks.filter((t) => !existingSet.has(t.trim()))
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="bg-card rounded-2xl shadow-lg max-w-sm w-full p-6 space-y-4">
        <h2 className="text-lg font-bold text-foreground">合并作业清单</h2>
        {skipped.length > 0 && (
          <p className="text-sm text-muted-foreground">
            已跳过 {skipped.length} 个重复任务
          </p>
        )}
        {added.length > 0 && (
          <div className="bg-primary/5 rounded-xl p-3 text-sm space-y-1">
            <p className="font-medium text-primary">新增 {added.length} 个任务：</p>
            {added.map((t, i) => (
              <p key={i} className="text-foreground">+ {t}</p>
            ))}
          </div>
        )}
        {added.length === 0 && (
          <p className="text-sm text-muted-foreground">所有任务已存在，无需合并</p>
        )}
        <div className="flex gap-3 pt-2">
          <button onClick={onCancel} className="flex-1 rounded-xl border border-border py-2.5 text-sm text-foreground hover:bg-muted transition">
            取消
          </button>
          <button onClick={onConfirm} className="flex-1 rounded-xl bg-primary text-primary-foreground py-2.5 text-sm font-medium hover:opacity-90 transition">
            确认合并
          </button>
        </div>
      </div>
    </div>
  );
}

export default function UploadPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <UploadContent />
    </Suspense>
  );
}
