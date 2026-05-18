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
import { parseHistoryApi } from "@/lib/api";

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
const LS_KEY = "yomi_parse_history";

// ─── 三层加载（IDB → localStorage → 后端API）────────────
async function loadHistory(): Promise<JobHistoryEntry[]> {
  // 1. 优先 IndexedDB
  try {
    await purgeExpiredHomeworkHistory();
    const entries = await loadHomeworkHistory();
    if (entries.length > 0) {
      return entries
        .map(e => ({
          job_id: e.job_id,
          file_name: e.title,
          questions_count: e.question_count,
          status: "completed" as const,
          created_at: e.created_at,
          questions_snapshot: e.questions_snapshot,
        }))
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
  } catch { /* IDB 不可用 */ }

  // 2. 回退 localStorage
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed: JobHistoryEntry[] = JSON.parse(raw);
      if (parsed.length > 0) {
        const cutoff = Date.now() - MAX_AGE_MS;
        const valid = parsed.filter(e => new Date(e.created_at).getTime() > cutoff);
        if (valid.length > 0) {
          // 迁移到 IDB
          valid.forEach(e => {
            const entry: HomeworkHistoryEntry = {
              local_id: uuidv4(),
              job_id: e.job_id,
              title: e.file_name || "作业记录",
              created_at: e.created_at,
              expires_at: new Date(new Date(e.created_at).getTime() + MAX_AGE_MS).toISOString(),
              question_count: e.questions_count,
              questions_snapshot: e.questions_snapshot,
            };
            saveHomeworkEntry(entry).catch(() => {});
          });
          return valid;
        }
      }
    }
  } catch { /* LS 不可用 */ }

  // 3. 后端 API 兜底（清缓存后恢复）
  try {
    const res = await parseHistoryApi.getHistory();
    if (res.ok && res.data && res.data.length > 0) {
      const entries: JobHistoryEntry[] = res.data
        .flatMap(d => {
          try {
            const parsed = JSON.parse(d.data_json);
            return Array.isArray(parsed) ? parsed : [parsed];
          } catch { return []; }
        });
      const cutoff = Date.now() - MAX_AGE_MS;
      const valid = entries.filter(e => new Date(e.created_at).getTime() > cutoff);
      if (valid.length > 0) {
        // 恢复到本地
        localStorage.setItem(LS_KEY, JSON.stringify(valid));
        valid.forEach(e => {
          const entry: HomeworkHistoryEntry = {
            local_id: uuidv4(),
            job_id: e.job_id,
            title: e.file_name || "作业记录",
            created_at: e.created_at,
            expires_at: new Date(new Date(e.created_at).getTime() + MAX_AGE_MS).toISOString(),
            question_count: e.questions_count,
            questions_snapshot: e.questions_snapshot,
          };
          saveHomeworkEntry(entry).catch(() => {});
        });
        return valid;
      }
    }
  } catch { /* API 不可用 */ }

  return [];
}

export function useJobHistory() {
  const [history, setHistory] = useState<JobHistoryEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    loadHistory().then(entries => {
      if (!cancelled) setHistory(entries);
    });
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

      // 三写：localStorage + IDB + 后端
      try { localStorage.setItem(LS_KEY, JSON.stringify(valid)); } catch {}
      (async () => {
        try {
          await purgeExpiredHomeworkHistory();
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
          // 异步写后端
          parseHistoryApi.saveHistory(entry.job_id, JSON.stringify([entry])).catch(() => {});
        } catch { /* 静默降级 */ }
      })();
      return valid;
    });
  }, []);

  const removeEntry = useCallback((jobId: string) => {
    setHistory(prev => {
      const entries = prev.filter(e => e.job_id !== jobId);
      try { localStorage.setItem(LS_KEY, JSON.stringify(entries)); } catch {}
      return entries;
    });
    deleteHomeworkByJobId(jobId).catch(() => {});
  }, []);

  const clearAll = useCallback(async () => {
    setHistory([]);
    try { localStorage.removeItem(LS_KEY); } catch {}
    try {
      const all = await loadHomeworkHistory();
      await Promise.all(all.map(e => deleteHomeworkEntry(e.local_id)));
    } catch {}
  }, []);

  return { history, upsert, removeEntry, clearAll };
}
