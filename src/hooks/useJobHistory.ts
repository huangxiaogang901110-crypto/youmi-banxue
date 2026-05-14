"use client";

import { useState, useCallback, useEffect } from "react";
import {
  loadHomeworkHistory,
  saveHomeworkEntry,
  deleteHomeworkByJobId,
  deleteHomeworkEntry,
  purgeExpiredHomeworkHistory,
  getHomeworkByJobId,
  type HomeworkHistoryEntry,
  type QuestionSnapshot,
} from "@/lib/localCache";
import { uuidv4 } from "@/lib/uuid";

export interface JobHistoryEntry {
  job_id: string;
  file_name: string;
  questions_count: number;
  status: string;
  created_at: string;
  image_hash?: string;
  questions_snapshot?: QuestionSnapshot[];
}

const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

export function useJobHistory() {
  const [history, setHistory] = useState<JobHistoryEntry[]>([]);

  // 仅在客户端挂载后从 IndexedDB 加载
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await purgeExpiredHomeworkHistory();
        const entries = await loadHomeworkHistory();
        if (cancelled) return;
        const mapped: JobHistoryEntry[] = entries
          .map(e => ({
            job_id: e.job_id,
            file_name: e.title,
            questions_count: e.question_count,
            status: "completed",
            created_at: e.created_at,
            questions_snapshot: e.questions_snapshot,
          }))
          .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
        setHistory(mapped);
      } catch { /* IndexedDB 不可用时静默降级 */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const upsert = useCallback((entry: JobHistoryEntry) => {
    setHistory(prev => {
      const entries = [...prev];
      const idx = entries.findIndex(e => e.job_id === entry.job_id);
      if (idx >= 0) {
        entries[idx] = { ...entries[idx], ...entry };
      } else {
        entries.unshift(entry);
      }
      const now = Date.now();
      const valid = entries.filter(e => now - new Date(e.created_at).getTime() < MAX_AGE_MS);
      // 异步写 IndexedDB（不阻塞 UI）
      (async () => {
        try {
          await purgeExpiredHomeworkHistory();
          // 检查是否已存在同 job_id 的记录（幂等）
          const existing = await getHomeworkByJobId(entry.job_id);
          const localId = existing?.local_id || uuidv4();
          const created = new Date(entry.created_at).getTime();
          await saveHomeworkEntry({
            local_id: localId,
            job_id: entry.job_id,
            title: entry.file_name || "作业记录",
            created_at: entry.created_at,
            expires_at: new Date(created + MAX_AGE_MS).toISOString(),
            question_count: entry.questions_count,
            questions_snapshot: entry.questions_snapshot,
          });
        } catch { /* 静默降级 */ }
      })();
      return valid;
    });
  }, []);

  const removeEntry = useCallback((jobId: string) => {
    setHistory(prev => prev.filter(e => e.job_id !== jobId));
    deleteHomeworkByJobId(jobId).catch(() => {});
  }, []);

  const clearAll = useCallback(async () => {
    setHistory([]);
    const all = await loadHomeworkHistory();
    await Promise.all(all.map(e => deleteHomeworkEntry(e.local_id)));
  }, []);

  return { history, upsert, removeEntry, clearAll };
}
