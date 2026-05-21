"""
悠米伴学 — 图片指纹模块
仅负责计算和存储图片内容指纹，不查重、不复用。
"""
import hashlib
import io
from typing import Optional
from PIL import Image, ExifTags

HASH_SIZE = 8  # ahash/dhash 缩略图尺寸


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ahash(img: Image.Image) -> str:
    """Average Hash：8×8 灰度，与均值比较 → 64-bit hex"""
    gray = img.convert("L").resize((HASH_SIZE, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return hex(int(bits, 2))[2:].zfill(16)


def dhash(img: Image.Image) -> str:
    """Difference Hash：9×8 灰度，水平梯度 → 64-bit hex"""
    gray = img.convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    pixels = list(gray.getdata())
    bits = ""
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * (HASH_SIZE + 1) + col]
            right = pixels[row * (HASH_SIZE + 1) + col + 1]
            bits += "1" if left > right else "0"
    return hex(int(bits, 2))[2:].zfill(16)


def compute_fingerprints(image_bytes: bytes) -> dict:
    """
    对上传图片 bytes 计算所有指纹字段。
    异常时返回 null 值字典，不抛异常。
    """
    result = {
        "original_sha256": sha256_hex(image_bytes),
        "ahash": None,
        "dhash": None,
        "width": None,
        "height": None,
        "aspect_ratio": None,
        "file_size": len(image_bytes),
    }
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # 修正 EXIF 旋转
        try:
            exif = img._getexif()
            if exif:
                for tag, value in exif.items():
                    if ExifTags.TAGS.get(tag) == "Orientation":
                        if value == 3:
                            img = img.rotate(180, expand=True)
                        elif value == 6:
                            img = img.rotate(270, expand=True)
                        elif value == 8:
                            img = img.rotate(90, expand=True)
        except Exception:
            pass

        if img.mode != "RGB":
            img = img.convert("RGB")

        result["width"] = img.size[0]
        result["height"] = img.size[1]
        result["aspect_ratio"] = round(img.size[0] / img.size[1], 4) if img.size[1] else None
        result["ahash"] = ahash(img)
        result["dhash"] = dhash(img)
    except Exception:
        pass

    return result
