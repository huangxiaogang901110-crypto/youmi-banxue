/**
 * 图片客户端压缩工具
 * 基准：长边 ≤1600px, JPEG quality 0.8, ≤2MB
 * 拍照和本地上传均走此函数。压缩失败时 fallback 原图。
 */

export interface CompressResult {
  file: File;
  originalSize: number;
  compressedSize: number;
  originalWidth?: number;
  originalHeight?: number;
  compressedWidth?: number;
  compressedHeight?: number;
}

const MAX_LONG_SIDE = 1600;
const MAX_SIZE = 2 * 1024 * 1024;  // 2MB
const QUALITY = 0.8;
const QUALITY_FALLBACK = 0.6;

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
    console.warn("[compress] Canvas 不可用，跳过压缩");
    return { file, originalSize, compressedSize: originalSize };
  }

  const img = await loadImage(file);
  const origW = img.naturalWidth;
  const origH = img.naturalHeight;
  const { width, height } = calcSize(origW, origH, MAX_LONG_SIDE);

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
    let blob = await compress(QUALITY);
    if (!blob) throw new Error("canvas.toBlob 返回 null");

    if (blob.size > MAX_SIZE) {
      blob = await compress(QUALITY_FALLBACK);
      if (!blob) throw new Error("二次压缩失败");
    }

    const compressedFile = new File([blob], file.name, { type: "image/jpeg" });
    console.warn(
      `[compress] ${(originalSize / 1024).toFixed(0)}KB(${origW}x${origH}) → ${(compressedFile.size / 1024).toFixed(0)}KB(${width}x${height})`
    );
    return {
      file: compressedFile,
      originalSize,
      compressedSize: compressedFile.size,
      originalWidth: origW,
      originalHeight: origH,
      compressedWidth: width,
      compressedHeight: height,
    };
  } catch {
    console.warn(`[compress] 压缩失败，使用原图 (${(originalSize / 1024).toFixed(0)}KB)`);
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
