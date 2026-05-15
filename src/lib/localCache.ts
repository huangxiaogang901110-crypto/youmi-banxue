const DB_NAME = "yomi_cache";
const DB_VERSION = 2;

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("tutor_results")) {
        db.createObjectStore("tutor_results", { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains("vision_results")) {
        db.createObjectStore("vision_results", { keyPath: "id" });
      }
      // v2: 作业清单历史记录
      if (!db.objectStoreNames.contains("homework_history")) {
        const store = db.createObjectStore("homework_history", { keyPath: "local_id" });
        store.createIndex("job_id", "job_id", { unique: false });
        store.createIndex("created_at", "created_at", { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function saveTutorResult(questionId: string, action: string, data: unknown) {
  const db = await openDB();
  const key = `${questionId}::${action}`;
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("tutor_results", "readwrite");
    tx.objectStore("tutor_results").put({ id: key, data, savedAt: Date.now() });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function loadTutorResult(questionId: string, action: string): Promise<unknown | null> {
  const db = await openDB();
  const key = `${questionId}::${action}`;
  return new Promise((resolve, reject) => {
    const tx = db.transaction("tutor_results", "readonly");
    const req = tx.objectStore("tutor_results").get(key);
    req.onsuccess = () => resolve(req.result?.data ?? null);
    req.onerror = () => reject(req.error);
  });
}

export async function saveVisionResult(questionId: string, data: unknown) {
  const db = await openDB();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("vision_results", "readwrite");
    tx.objectStore("vision_results").put({ id: questionId, data, savedAt: Date.now() });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function loadVisionResult(questionId: string): Promise<unknown | null> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("vision_results", "readonly");
    const req = tx.objectStore("vision_results").get(questionId);
    req.onsuccess = () => resolve(req.result?.data ?? null);
    req.onerror = () => reject(req.error);
  });
}

// ── homework_history IndexedDB CRUD ──

export interface HomeworkHistoryEntry {
  local_id: string;      // UUID 主键
  job_id: string;        // 后端 parse job id
  client_task_id?: string;
  child_id?: string;
  title: string;         // "数学作业 · 27题" 或文件名
  created_at: string;    // ISO
  expires_at: string;    // ISO = created_at + 7天
  question_count: number;
  completed_count?: number;
  wrong_count?: number;
  questions_snapshot?: QuestionSnapshot[];  // 题目清单摘要
}

export interface QuestionSnapshot {
  question_id: string;
  question_number: number;
  question_text: string;    // 前 60 字符
  is_correct?: boolean | null;
  student_answer?: string | null;
}

const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 天

export async function purgeExpiredHomeworkHistory(): Promise<void> {
  try {
    const db = await openDB();
    const tx = db.transaction("homework_history", "readwrite");
    const store = tx.objectStore("homework_history");
    const idx = store.index("created_at");
    const cutoff = new Date(Date.now() - MAX_AGE_MS).toISOString();
    const range = IDBKeyRange.upperBound(cutoff);
    const req = idx.openCursor(range);
    req.onsuccess = () => {
      const cursor = req.result;
      if (cursor) {
        cursor.delete();
        cursor.continue();
      }
    };
    await new Promise<void>((resolve) => { tx.oncomplete = () => resolve(); });
  } catch { /* 静默降级 */ }
}

export async function saveHomeworkEntry(entry: HomeworkHistoryEntry): Promise<void> {
  const db = await openDB();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("homework_history", "readwrite");
    tx.objectStore("homework_history").put(entry);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function loadHomeworkHistory(): Promise<HomeworkHistoryEntry[]> {
  const db = await openDB();
  return new Promise<HomeworkHistoryEntry[]>((resolve, reject) => {
    const tx = db.transaction("homework_history", "readonly");
    const req = tx.objectStore("homework_history").getAll();
    req.onsuccess = () => {
      const all = (req.result || []) as HomeworkHistoryEntry[];
      const now = Date.now();
      resolve(all.filter(e => now - new Date(e.created_at).getTime() < MAX_AGE_MS));
    };
    req.onerror = () => reject(req.error);
  });
}

export async function getHomeworkByJobId(jobId: string): Promise<HomeworkHistoryEntry | null> {
  const db = await openDB();
  return new Promise<HomeworkHistoryEntry | null>((resolve, reject) => {
    const tx = db.transaction("homework_history", "readonly");
    const idx = tx.objectStore("homework_history").index("job_id");
    const req = idx.get(jobId);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

export async function deleteHomeworkEntry(localId: string): Promise<void> {
  const db = await openDB();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("homework_history", "readwrite");
    tx.objectStore("homework_history").delete(localId);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function deleteHomeworkByJobId(jobId: string): Promise<void> {
  const db = await openDB();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("homework_history", "readwrite");
    const idx = tx.objectStore("homework_history").index("job_id");
    const req = idx.getKey(jobId);
    req.onsuccess = () => {
      if (req.result) {
        tx.objectStore("homework_history").delete(req.result);
      }
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function clearAllCache() {
  // 1. 清除 IndexedDB 所有 store
  const db = await openDB();
  await new Promise<void>((resolve, reject) => {
    const stores = ["tutor_results", "vision_results", "homework_history"];
    const tx = db.transaction(stores, "readwrite");
    for (const name of stores) {
      tx.objectStore(name).clear();
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });

  // 2. 清除 localStorage 缓存（保留登录 token）
  const LOCAL_KEYS = [
    "yomi_job_history",
    "yomi_deleted_jobs",
    "yomi_homework_days",
    "yomi_homework_subjects",
    "yomi_homework_done",
  ];
  for (const key of LOCAL_KEYS) {
    try { localStorage.removeItem(key); } catch { /* ignore */ }
  }
}
