/** 生成 UUIDv4（浏览器 crypto.randomUUID） */
export function uuidv4(): string {
  return crypto.randomUUID();
}

/** 生成短 ID（用于非安全场景的显示） */
export function shortId(): string {
  return uuidv4().slice(0, 8);
}
