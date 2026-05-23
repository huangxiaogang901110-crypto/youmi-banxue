"""旁路预处理。

第一阶段只生成对比版本和映射元数据，不替换主链路输入。
当前实现使用 Pillow 做轻量近似版：
- v_clean: 灰度 + 自动对比度 + 中值去噪
- v_binary: 在 v_clean 基础上做轻量二值化

本阶段不做真实几何变换，transform_matrix 保持单位阵。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from schemas.recognition import PreprocessVersion, RecognitionImage, identity_transform_matrix


def _require_pillow():
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    return Image, ImageEnhance, ImageFilter, ImageOps


def _serialize_png(image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _save_version_bytes(output_dir: Path, version_id: str, image_bytes: bytes) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{version_id}.png"
    output_path.write_bytes(image_bytes)
    return str(output_path)


def generate_preprocess_bundle(
    image_bytes: bytes,
    image_id: str,
    *,
    source_path: str | None = None,
    output_dir: str | None = None,
) -> tuple[RecognitionImage, dict[str, bytes]]:
    Image, ImageEnhance, ImageFilter, ImageOps = _require_pillow()

    src = Image.open(BytesIO(image_bytes)).convert("RGB")
    gray = ImageOps.grayscale(src)
    clean = ImageOps.autocontrast(gray)
    clean = ImageEnhance.Contrast(clean).enhance(1.15)
    clean = clean.filter(ImageFilter.MedianFilter(size=3))

    binary = clean.filter(ImageFilter.GaussianBlur(radius=0.6))
    binary = binary.point(lambda value: 255 if value > 160 else 0, mode="1").convert("L")

    artifact_bytes = {
        "v_original": image_bytes,
        "v_clean": _serialize_png(clean),
        "v_binary": _serialize_png(binary),
    }

    output_root = Path(output_dir) if output_dir else None

    preprocess_versions = [
        PreprocessVersion(
            id=f"{image_id}-v_clean",
            text="clean",
            source="pillow_sidecar",
            kind="preprocess_version",
            status="completed",
            image_path=_save_version_bytes(output_root, f"{image_id}-v_clean", artifact_bytes["v_clean"]) if output_root else None,
            width=clean.width,
            height=clean.height,
            transform_matrix=identity_transform_matrix(),
            operations=[
                {"name": "grayscale", "params": {}},
                {"name": "autocontrast", "params": {}},
                {"name": "contrast", "params": {"factor": 1.15}},
                {"name": "median_filter", "params": {"size": 3}},
            ],
            notes="保留笔迹细节，供 OCR/答案定位对比。",
        ),
        PreprocessVersion(
            id=f"{image_id}-v_binary",
            text="binary",
            source="pillow_sidecar",
            kind="preprocess_version",
            status="completed",
            image_path=_save_version_bytes(output_root, f"{image_id}-v_binary", artifact_bytes["v_binary"]) if output_root else None,
            width=binary.width,
            height=binary.height,
            transform_matrix=identity_transform_matrix(),
            operations=[
                {"name": "gaussian_blur", "params": {"radius": 0.6}},
                {"name": "threshold", "params": {"cutoff": 160}},
            ],
            notes="轻量二值旁路版本，供检测对比使用。",
        ),
    ]

    image = RecognitionImage(
        id=image_id,
        text=Path(source_path).name if source_path else image_id,
        source="upload",
        kind="image",
        status="completed",
        file_path=source_path,
        width=src.width,
        height=src.height,
        preprocess_versions=preprocess_versions,
    )
    return image, artifact_bytes


def preprocess_file(file_path: str, *, output_dir: str | None = None) -> tuple[RecognitionImage, dict[str, bytes]]:
    path = Path(file_path)
    return generate_preprocess_bundle(
        path.read_bytes(),
        path.stem,
        source_path=str(path),
        output_dir=output_dir,
    )
