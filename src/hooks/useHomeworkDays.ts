"use client";

import { useState, useCallback, useEffect } from "react";
import type { HomeworkSubject } from "@/lib/types";

export interface HomeworkDayEntry {
  date: string; // "2026-05-11"
  raw_text: string;
  subjects: HomeworkSubject[];
  doneMap: Record<string, boolean>; // "subjectName||taskText" → boolean
  created_at: string; // ISO
}

const LS_KEY = "yomi_homework_days";
const MAX_AGE_DAYS = 30; // 最多保留 30 天

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function makeKey(subjectName: string, taskText: string): string {
  return `${subjectName}||${taskText.trim()}`;
}

function loadDays(): HomeworkDayEntry[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const entries: HomeworkDayEntry[] = JSON.parse(raw);
    // 清理过期
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - MAX_AGE_DAYS);
    const valid = entries.filter((e) => new Date(e.created_at) > cutoff);
    if (valid.length < entries.length) {
      localStorage.setItem(LS_KEY, JSON.stringify(valid));
    }
    return valid;
  } catch {
    return [];
  }
}

function saveDays(entries: HomeworkDayEntry[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(entries));
  } catch {}
}

export function useHomeworkDays() {
  const [days, setDays] = useState<HomeworkDayEntry[]>([]);

  useEffect(() => {
    setDays(loadDays());
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
    setDays([]);
  }, []);

  return { days, getToday, getHistory, upsertToday, toggleTask, deleteTask, copyToToday, removeDay, clearAll };
}
