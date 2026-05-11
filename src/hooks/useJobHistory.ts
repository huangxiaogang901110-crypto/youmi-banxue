"use client";

import { useState, useCallback, useEffect } from "react";

export interface JobHistoryEntry {
  job_id: string;
  file_name: string;
  questions_count: number;
  status: string;
  created_at: string; // ISO
}

const LS_KEY = "yomi_job_history";
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 天

function loadHistory(): JobHistoryEntry[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const entries: JobHistoryEntry[] = JSON.parse(raw);
    const now = Date.now();
    return entries.filter((e) => now - new Date(e.created_at).getTime() < MAX_AGE_MS);
  } catch {
    return [];
  }
}

function saveHistory(entries: JobHistoryEntry[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(entries));
  } catch { /* quota exceeded */ }
}

export function useJobHistory() {
  const [history, setHistory] = useState<JobHistoryEntry[]>([]);

  // 仅在客户端挂载后加载
  useEffect(() => {
    setHistory(loadHistory());
  }, []);

  const upsert = useCallback((entry: JobHistoryEntry) => {
    setHistory((prev) => {
      const entries = [...prev];
      const idx = entries.findIndex((e) => e.job_id === entry.job_id);
      if (idx >= 0) {
        entries[idx] = { ...entries[idx], ...entry };
      } else {
        entries.unshift(entry);
      }
      const now = Date.now();
      const valid = entries.filter((e) => now - new Date(e.created_at).getTime() < MAX_AGE_MS);
      saveHistory(valid);
      return valid;
    });
  }, []);

  const removeEntry = useCallback((jobId: string) => {
    setHistory((prev) => {
      const entries = prev.filter((e) => e.job_id !== jobId);
      saveHistory(entries);
      return entries;
    });
  }, []);

  const clearAll = useCallback(() => {
    try {
      localStorage.removeItem(LS_KEY);
    } catch { /* ignore */ }
    setHistory([]);
  }, []);

  return { history, upsert, removeEntry, clearAll };
}
