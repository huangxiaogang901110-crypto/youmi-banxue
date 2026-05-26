import json
import os
import time

from logger import warning


EXPORT_ENV_VARS = ("YOMI_EXPORT_OCR_BLOCKS", "YOMIEXPORTOCRBLOCKS")


def export_ocr_blocks_if_enabled(job_id, ocr_blocks):
    if not any(env_name in os.environ for env_name in EXPORT_ENV_VARS):
        return
    if not ocr_blocks:
        return

    try:
        with open(f"/tmp/ocrblocks_{job_id}.json", "w", encoding="utf-8") as export_file:
            json.dump(
                {
                    "job_id": job_id,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "blocks": ocr_blocks,
                },
                export_file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        warning(f"[BG] OCR blocks export failed for {job_id}: {exc}")
