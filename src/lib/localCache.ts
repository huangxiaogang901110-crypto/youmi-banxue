const DB_NAME = "yomi_cache";
const DB_VERSION = 1;

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
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function saveTutorResult(questionId: string, data: unknown) {
  const db = await openDB();
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction("tutor_results", "readwrite");
    tx.objectStore("tutor_results").put({ id: questionId, data, savedAt: Date.now() });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function loadTutorResult(questionId: string): Promise<unknown | null> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("tutor_results", "readonly");
    const req = tx.objectStore("tutor_results").get(questionId);
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
export async function clearAllCache() {
  // 1. 清除 IndexedDB
  const db = await openDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(["tutor_results", "vision_results"], "readwrite");
    tx.objectStore("tutor_results").clear();
    tx.objectStore("vision_results").clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });

  // 2. 清除 localStorage（保留登录 token，登出单独处理）
  const LOCAL_KEYS = [
    "yomi_job_history",
    "yomi_homework_days",
    "yomi_homework_subjects",
    "yomi_homework_done",
  ];
  for (const key of LOCAL_KEYS) {
    try { localStorage.removeItem(key); } catch {}
  }
}
