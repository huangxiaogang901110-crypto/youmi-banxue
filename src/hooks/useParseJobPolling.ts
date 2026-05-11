"use client";

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
  const jobId = searchParams.get("job_id");

  const jobQuery = useQuery({
    queryKey: ["parseJob", jobId],
    queryFn: (): Promise<ApiResponse<ParseJob>> => parseJobApi.getStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data?.ok || !data.data) return false;
      if (data.data.status === "completed" || data.data.status === "failed") return false;
      return 1500;
    },
  });

  const questionsQuery = useQuery({
    queryKey: ["questions", jobId],
    queryFn: (): Promise<ApiResponse<Question[]>> => parseJobApi.getQuestions(jobId!),
    enabled: !!jobId && jobQuery.data?.ok && jobQuery.data.data?.status === "completed",
  });

  if (!jobId) return { job: null, questions: null, status: "idle", error: "" };

  if (jobQuery.isLoading) return { job: null, questions: null, status: "loading", error: "" };
  if (jobQuery.error) return { job: null, questions: null, status: "failed", error: "获取任务状态失败" };

  // API 返回 ok:false 视为错误（如 poll_count 缺失等后端异常）
  if (!jobQuery.data?.ok) {
    return { job: null, questions: null, status: "failed", error: jobQuery.data?.message || "服务器异常，请重试" };
  }

  const job = jobQuery.data?.ok ? (jobQuery.data.data ?? null) : null;
  const s = job?.status;

  if (s === "failed") return { job, questions: null, status: "failed", error: "解析失败，请重新上传" };
  if (s === "completed") {
    const qs = questionsQuery.data?.ok ? (questionsQuery.data.data ?? null) : null;
    return { job, questions: qs, status: "completed", error: "" };
  }
  return { job, questions: null, status: "polling", error: "" };
}
