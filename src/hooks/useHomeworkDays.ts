"use client";

import { useState, useCallback, useEffect } from "react";
import type { HomeworkSubject } from "@/lib/types";
import { idbGet, idbSet, idbDel } from "@/lib/idbStorage";
import { homeworkApi } from "@/lib/api";

export interface HomeworkDayEntry {
  date: string; // "2026-05-11"
  raw_text: string;
  subjects: HomeworkSubject[];
  doneMap: Record<string, boolean>; // "subjectName||taskText" → boolean
  created_at: string; // ISO
}

const LS_KEY = "yomi_homework_days";
const MAX_AGE_DAYS = 7; // 最多保留 7 天

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function makeKey(subjectName: string, taskText: string): string {
  return `${subjectName}||${taskText.trim()}`;
}

async function loadDays(): Promise<HomeworkDayEntry[]> {
  const diag = (...args: unknown[]) => console.log("[HW-DIAG] loadDays:", ...args);

  // 1. 优先 IndexedDB（清缓存不清 IDB）
  try {
    const raw = await idbGet(LS_KEY);
    diag("step1-IDB raw?", raw !== null, "len:", raw?.length ?? 0);
    if (raw) {
      const entries: HomeworkDayEntry[] = JSON.parse(raw);
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - MAX_AGE_DAYS);
      const valid = entries.filter((e) => new Date(e.created_at) > cutoff);
      diag("step1-IDB entries:", entries.length, "valid:", valid.length);
      if (valid.length < entries.length) {
        idbSet(LS_KEY, JSON.stringify(valid));
      }
      return valid;
    }
  } catch (e) { diag("step1-IDB ERROR", e); /* fall through */ }

  // 2. 回退 localStorage
  try {
    const raw = localStorage.getItem(LS_KEY);
    diag("step2-LS raw?", raw !== null, "len:", raw?.length ?? 0);
    if (raw) {
      const entries: HomeworkDayEntry[] = JSON.parse(raw);
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - MAX_AGE_DAYS);
      const valid = entries.filter((e) => new Date(e.created_at) > cutoff);
      diag("step2-LS entries:", entries.length, "valid:", valid.length);
      if (valid.length > 0) {
        idbSet(LS_KEY, JSON.stringify(valid)); // 迁移到 IDB
        return valid;
      }
    }
  } catch (e) { diag("step2-LS ERROR", e); /* fall through */ }

  // 3. 后端兜底（清缓存/换设备后恢复）
  try {
    diag("step3-API calling homeworkApi.getDays()...");
    const res = await homeworkApi.getDays();
    diag("step3-API res.ok:", res.ok, "data_len:", res.data?.length ?? 0, "code:", (res as any).code, "message:", (res as any).message);
    if (!res.ok) {
      const code = (res as any).code;
      if (code === 'unauthorized' || code === 'token_expired') {
        console.warn('[HW-DIAG] loadDays step3-API auth failed, skipping backend restore:', code);
      }
      // fall through to return []
    }
    if (res.ok && res.data && res.data.length > 0) {
      const entries: HomeworkDayEntry[] = res.data
        .flatMap((d) => {
          try {
            const parsed = JSON.parse(d.data_json);
            return Array.isArray(parsed) ? parsed : [parsed];
          } catch { return []; }
        });
      diag("step3-API parsed entries:", entries.length);
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - MAX_AGE_DAYS);
      const valid = entries.filter((e) => new Date(e.created_at) > cutoff);
      diag("step3-API valid after cutoff:", valid.length);
      if (valid.length > 0) {
        // 恢复到本地
        localStorage.setItem(LS_KEY, JSON.stringify(valid));
        idbSet(LS_KEY, JSON.stringify(valid));
        return valid;
      }
    }
  } catch (e) { diag("step3-API ERROR", e); /* ignore */ }

  diag("FINAL return [] — all steps failed or empty");
  return [];
}

function saveDays(entries: HomeworkDayEntry[]) {
  const d = new Date().toISOString().slice(0, 10);
  console.log("[HW-DIAG] saveDays called, entries:", entries.length, "today:", d);
  // 同步写 localStorage（React setState 回调需要同步）
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(entries));
    console.log("[HW-DIAG] saveDays localStorage OK");
  } catch (e) { console.log("[HW-DIAG] saveDays localStorage ERROR", e); }
  // 异步写 IndexedDB
  idbSet(LS_KEY, JSON.stringify(entries)).then(
    () => console.log("[HW-DIAG] saveDays IDB OK"),
    (e) => console.log("[HW-DIAG] saveDays IDB ERROR", e),
  );
  // 异步写后端
  homeworkApi.saveDays(d, JSON.stringify(entries)).then(
    (res) => console.log("[HW-DIAG] saveDays API res:", res.ok, (res as any).code, (res as any).message),
    (e) => console.log("[HW-DIAG] saveDays API ERROR", e),
  );
}

export function useHomeworkDays() {
  const [days, setDays] = useState<HomeworkDayEntry[]>([]);

  useEffect(() => {
    loadDays().then(setDays);
  }, []);

  const getToday = useCallback((): HomeworkDayEntry | undefined => {
    const t = today();
    return days.find((d) => d.date === t);
  }, [days]);

  const getHistory = useCallback((): HomeworkDayEntry[] => {
    const t = today();
    return days.filter((d) => d.date !== t).sort((a, b) => b.date.localeCompare(a.date));
  }, [days]);

  /** 粘贴解析后写入今天 */
  const upsertToday = useCallback(
    (subjects: HomeworkSubject[], rawText: string) => {
      const t = today();
      setDays((prev) => {
        const entries = [...prev];
        const idx = entries.findIndex((e) => e.date === t);
        if (idx >= 0) {
          // 合并：同名科目追加新任务，去重
          const existing = entries[idx];
          const allExistingTasks = new Set(
            existing.subjects.flatMap((s) => s.tasks.map((t) => t.trim()))
          );
          const merged = existing.subjects.map((s) => ({ ...s, tasks: [...s.tasks] }));
          for (const inc of subjects) {
            const newTasks = inc.tasks.filter((tk) => !allExistingTasks.has(tk.trim()));
            if (newTasks.length === 0) continue;
            const exist = merged.find((m) => m.name === inc.name);
            if (exist) {
              exist.tasks.push(...newTasks);
            } else {
              merged.push({ name: inc.name, tasks: newTasks });
            }
            newTasks.forEach((tk) => allExistingTasks.add(tk.trim()));
          }
          entries[idx] = {
            ...existing,
            subjects: merged,
            raw_text: existing.raw_text + "\n---\n" + rawText,
          };
        } else {
          // 新建今日条目
          const doneMap: Record<string, boolean> = {};
          for (const s of subjects) {
            for (const t of s.tasks) {
              doneMap[makeKey(s.name, t)] = false;
            }
          }
          entries.unshift({
            date: t,
            raw_text: rawText,
            subjects,
            doneMap,
            created_at: new Date().toISOString(),
          });
        }
        saveDays(entries);
        return entries;
      });
    },
    []
  );

  const toggleTask = useCallback((date: string, subjectName: string, taskText: string) => {
    const key = makeKey(subjectName, taskText);
    setDays((prev) => {
      const entries = prev.map((d) =>
        d.date === date ? { ...d, doneMap: { ...d.doneMap, [key]: !d.doneMap[key] } } : d
      );
      saveDays(entries);
      return entries;
    });
  }, []);

  const deleteTask = useCallback((date: string, subjectName: string, taskIdx: number) => {
    setDays((prev) => {
      const entries = prev.map((d) => {
        if (d.date !== date) return d;
        const updated = d.subjects.map((s) => {
          if (s.name !== subjectName) return s;
          const newTasks = s.tasks.filter((_, i) => i !== taskIdx);
          return { ...s, tasks: newTasks };
        }).filter((s) => s.tasks.length > 0);
        return { ...d, subjects: updated };
      });
      saveDays(entries);
      return entries;
    });
  }, []);

  /** 把历史某天的作业复制到今日 */
  const copyToToday = useCallback((fromDate: string) => {
    const src = days.find((d) => d.date === fromDate);
    if (!src) return;
    const t = today();
    setDays((prev) => {
      const entries = [...prev];
      const idx = entries.findIndex((e) => e.date === t);
      if (idx >= 0) {
        // 合并
        const existing = entries[idx];
        const allExisting = new Set(
          existing.subjects.flatMap((s) => s.tasks.map((t) => t.trim()))
        );
        for (const ss of src.subjects) {
          const unticked = ss.tasks.filter(
            (tk) => !allExisting.has(tk.trim()) && !src.doneMap[makeKey(ss.name, tk)]
          );
          if (unticked.length === 0) continue;
          const exist = existing.subjects.find((m) => m.name === ss.name);
          if (exist) {
            exist.tasks.push(...unticked);
          } else {
            existing.subjects.push({ name: ss.name, tasks: unticked });
          }
          unticked.forEach((tk) => {
            existing.doneMap[makeKey(ss.name, tk)] = false;
            allExisting.add(tk.trim());
          });
        }
        entries[idx] = { ...existing };
      } else {
        // 新建：只复制未完成的任务
        const newSubjects: HomeworkSubject[] = [];
        const newDoneMap: Record<string, boolean> = {};
        for (const ss of src.subjects) {
          const unticked = ss.tasks.filter((tk) => !src.doneMap[makeKey(ss.name, tk)]);
          if (unticked.length > 0) {
            for (const tk of unticked) {
              newDoneMap[makeKey(ss.name, tk)] = false;
            }
            newSubjects.push({ name: ss.name, tasks: unticked });
          }
        }
        if (newSubjects.length > 0) {
          entries.unshift({
            date: t,
            raw_text: `来自 ${fromDate} 的未完成任务`,
            subjects: newSubjects,
            doneMap: newDoneMap,
            created_at: new Date().toISOString(),
          });
        }
      }
      saveDays(entries);
      return entries;
    });
  }, [days]);

  const removeDay = useCallback((date: string) => {
    setDays((prev) => {
      const entries = prev.filter((d) => d.date !== date);
      saveDays(entries);
      return entries;
    });
  }, []);

  const clearAll = useCallback(() => {
    try {
      localStorage.removeItem(LS_KEY);
    } catch {}
    idbDel(LS_KEY); // fire-and-forget
    setDays([]);
  }, []);

  return { days, getToday, getHistory, upsertToday, toggleTask, deleteTask, copyToToday, removeDay, clearAll };
}
