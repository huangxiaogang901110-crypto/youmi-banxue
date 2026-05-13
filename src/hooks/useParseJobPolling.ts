"use client";

import { useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { parseJobApi } from "@/lib/api";
import type { ApiResponse, ParseJob, Question } from "@/lib/types";

interface PollingResult {
  job: ParseJob | null;
  questions: Question[] | null;
  status: "idle" | "loading" | "polling" | "completed" | "failed";
  error: string;
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
      const s = data.data.status;
      // completed 持续轮询直到 questions 到位
      if (s === "completed" && emptyCompletedRef.current < 5) return 2000;
      if (s === "completed" || s === "failed") return false;
      return 1500;
    },
  });

  const questionsQuery = useQuery({
    queryKey: ["questions", jobId],
    queryFn: (): Promise<ApiResponse<Question[]>> => parseJobApi.getQuestions(jobId!),
    enabled: !!jobId && jobQuery.data?.ok && jobQuery.data.data?.status === "completed",
    retry: 3,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  });

  if (!jobId) return { job: null, questions: null, status: "idle", error: "" };

  if (jobQuery.isLoading) return { job: null, questions: null, status: "loading", error: "" };
  if (jobQuery.isError) return { job: null, questions: null, status: "polling", error: "" };

  if (!jobQuery.data?.ok) {
    return { job: null, questions: null, status: "polling", error: "" };
  }

  const job = jobQuery.data?.ok ? (jobQuery.data.data ?? null) : null;
  const s = job?.status;

  if (s === "failed") return { job, questions: null, status: "failed", error: "解析失败，请重新上传" };
  if (s === "completed") {
    const qs = questionsQuery.data?.ok ? (questionsQuery.data.data ?? null) : null;
    // qcount=0 防护：后端可能先标 completed 后写 questions，继续轮询
    if (!qs || qs.length === 0) {
      emptyCompletedRef.current += 1;
      if (emptyCompletedRef.current < 5) {
        return { job, questions: null, status: "polling", error: "" };
      }
    } else {
      emptyCompletedRef.current = 0;
    }
    return { job, questions: qs, status: "completed", error: "" };
  }
  return { job, questions: null, status: "polling", error: "" };
}
