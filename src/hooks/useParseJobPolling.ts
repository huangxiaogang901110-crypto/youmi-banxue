"use client";

import { useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { parseJobApi } from "@/lib/api";
import type { ApiResponse, ParseJob, Question } from "@/lib/types";
import type { OverlayMark, GroupBox, GradingMark } from "@/components/GradingOverlay";

/** 终态集合：停止轮询 */
const TERMINAL_STATUSES = new Set(["completed", "failed", "low_confidence", "needs_review"]);

/** 可展示结果的终态（有 questions 可读） */
const RESULT_STATUSES = new Set(["completed", "low_confidence", "needs_review"]);

/** 前端轮询状态 */
type PollStatus = "idle" | "loading" | "polling" | "completed" | "failed" | "low_confidence" | "needs_review";

interface PollingResult {
  job: ParseJob | null;
  questions: Question[] | null;
  overlay: OverlayMark[];
  group_boxes: GroupBox[];
  v2Marks: GradingMark[];
  v2NeedsReview: boolean;
  status: PollStatus;
  error: string;
}

/** 兼容后端命名变体 */
function normalizeJobStatus(s: string): string {
  if (s === "lowconfidence") return "low_confidence";
  if (s === "needsreview") return "needs_review";
  return s;
}

export function useParseJobPolling(): PollingResult {
  const searchParams = useSearchParams();
  const rawJobId = searchParams.get("job_id");
  const jobId = (rawJobId && rawJobId !== "undefined" && rawJobId !== "null" && rawJobId.trim())
    ? rawJobId
    : null;

  // completed 但 qcount=0 的重试计数器（最多再轮询 5 次）
  const emptyCompletedRef = useRef(0);

  const jobQuery = useQuery({
    queryKey: ["parseJob", jobId],
    queryFn: (): Promise<ApiResponse<ParseJob>> => parseJobApi.getStatus(jobId!),
    enabled: !!jobId,
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data?.ok || !data.data) return 1500;
      const s = normalizeJobStatus(data.data.status);
      // completed / low_confidence 持续轮询直到 questions 到位
      if (RESULT_STATUSES.has(s) && emptyCompletedRef.current < 5) return 2000;
      if (TERMINAL_STATUSES.has(s)) return false;
      return 1500;
    },
  });

  const questionsQuery = useQuery({
    queryKey: ["questions", jobId],
    queryFn: (): Promise<ApiResponse<Question[]>> => parseJobApi.getQuestions(jobId!),
    enabled: !!jobId && jobQuery.data?.ok && RESULT_STATUSES.has(normalizeJobStatus(jobQuery.data.data?.status ?? "")),
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });

  const EMPTY: PollingResult = { overlay: [], group_boxes: [], v2Marks: [], v2NeedsReview: false, job: null, questions: null, status: "idle", error: "" };

  if (!jobId) return EMPTY;

  if (jobQuery.isLoading) return { ...EMPTY, status: "loading" };
  if (jobQuery.isError) return { ...EMPTY, status: "polling" };

  if (!jobQuery.data?.ok) {
    return { ...EMPTY, status: "polling" };
  }

  const job = jobQuery.data?.ok ? (jobQuery.data.data ?? null) : null;
  const s = normalizeJobStatus(job?.status ?? "");

  if (s === "failed") return { ...EMPTY, job, status: "failed", error: "解析失败，请重新上传" };

  if (RESULT_STATUSES.has(s)) {
    const qs = questionsQuery.data?.ok ? (questionsQuery.data.data ?? null) : null;
    // qcount=0 防护：后端可能先标 completed/low_confidence 后写 questions，继续轮询
    if (!qs || qs.length === 0) {
      emptyCompletedRef.current += 1;
      if (emptyCompletedRef.current < 5) {
        return { ...EMPTY, job, status: "polling" };
      }
    } else {
      emptyCompletedRef.current = 0;
    }
    // low_confidence → 仍可展示结果，但带状态标记
    const raw_overlay = questionsQuery.data as any;
    const overlay: OverlayMark[] = raw_overlay?.overlay || [];
    const group_boxes: GroupBox[] = raw_overlay?.group_boxes || [];
    const v2Marks: GradingMark[] = raw_overlay?.v2_marks || [];
    const v2NeedsReview: boolean = raw_overlay?.v2_needs_review ?? false;
    return { overlay, group_boxes, v2Marks, v2NeedsReview, job, questions: qs, status: s as PollStatus, error: "" };
  }

  return { ...EMPTY, job, status: "polling" };
}
