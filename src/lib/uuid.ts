/**
 * 生成 UUIDv4，兼容 HTTP 环境。
 *
 * crypto.randomUUID() 仅在 HTTPS / localhost 下可用。
 * HTTP 公网部署时降级到 crypto.getRandomValues → Math.random 兜底。
 */
export function uuidv4(): string {
  // 优先：原生 randomUUID（HTTPS/localhost）
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  // 降级：crypto.getRandomValues
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const buf = new Uint8Array(16);
    crypto.getRandomValues(buf);
    buf[6] = (buf[6] & 0x0f) | 0x40; // version 4
    buf[8] = (buf[8] & 0x3f) | 0x80; // variant
    const hex = Array.from(buf, (b) => b.toString(16).padStart(2, "0"));
    return [
      hex[0] + hex[1] + hex[2] + hex[3],
      hex[4] + hex[5],
      hex[6] + hex[7],
      hex[8] + hex[9],
      hex[10] + hex[11] + hex[12] + hex[13] + hex[14] + hex[15],
    ].join("-");
  }

  // 兜底：Math.random（非安全场景足够）
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** 生成短 ID（用于非安全场景的显示） */
export function shortId(): string {
  return uuidv4().slice(0, 8);
}
