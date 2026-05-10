/**
 * 图片客户端压缩工具
 * 基准：前端基准 §8.3 — 长边 ≤2200px, JPEG quality 0.85, ≤3MB
 */

export interface CompressResult {
  file: File;
  originalSize: number;
  compressedSize: number;
}

const MAX_LONG_SIDE = 2200;
const SIZE_3MB = 3 * 1024 * 1024;

export async function compressImage(file: File): Promise<CompressResult> {
  const originalSize = file.size;

  // PDF 不压缩
  if (file.type === "application/pdf") {
    return { file, originalSize, compressedSize: originalSize };
  }

  // 非图片不压缩
  if (!file.type.startsWith("image/")) {
    return { file, originalSize, compressedSize: originalSize };
  }

  // 浏览器不支持 Canvas 时直接返回原文件
  if (typeof HTMLCanvasElement === "undefined") {
    return { file, originalSize, compressedSize: originalSize };
  }

  const img = await loadImage(file);
  const { width, height } = calcSize(img.naturalWidth, img.naturalHeight, MAX_LONG_SIDE);

  const compress = (quality: number): Promise<Blob | null> => {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    ctx.drawImage(img, 0, 0, width, height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  };

  try {
    let blob = await compress(0.85);
    if (!blob) throw new Error("压缩失败");

    if (blob.size > SIZE_3MB) {
      blob = await compress(0.6);
      if (!blob) throw new Error("二次压缩失败");
    }

    const compressedFile = new File([blob], file.name, { type: "image/jpeg" });
    return {
      file: compressedFile,
      originalSize,
      compressedSize: compressedFile.size,
    };
  } catch {
    return { file, originalSize, compressedSize: originalSize };
  }
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

function calcSize(w: number, h: number, max: number): { width: number; height: number } {
  if (w <= max && h <= max) return { width: w, height: h };
  const ratio = max / Math.max(w, h);
  return {
    width: Math.round(w * ratio),
    height: Math.round(h * ratio),
  };
}
