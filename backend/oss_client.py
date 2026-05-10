"""
悠米伴学 OSS 客户端 — 阿里云对象存储
图片上传/签名URL/删除，7 天生命周期由 Bucket Policy 控制
"""
import os
import uuid
from pathlib import Path

# Lazy import oss2 (避免启动时加载 C 扩展延迟)
_client_cache: dict = {}
_config_loaded = False
_bucket_name = ""
_endpoint = ""


def _load_config():
    global _config_loaded, _bucket_name, _endpoint
    if _config_loaded:
        return
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    _bucket_name = os.getenv("OSS_BUCKET", "youmi-images")
    _endpoint = os.getenv("OSS_ENDPOINT", "oss-cn-hangzhou.aliyuncs.com")
    _config_loaded = True


def _get_bucket():
    import oss2
    _load_config()
    key = (_bucket_name, _endpoint)
    if key not in _client_cache:
        ak_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        ak_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        auth = oss2.Auth(ak_id, ak_secret)
        _client_cache[key] = oss2.Bucket(auth, _endpoint, _bucket_name)
    return _client_cache[key]


def _available() -> bool:
    ak_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    return bool(ak_id and len(ak_id) > 10)


def upload_image(image_bytes: bytes, jid: str, suffix: str = "jpg") -> str | None:
    """
    上传图片到 OSS。返回 oss_key，失败返回 None。
    对象路径：images/{jid}.{suffix}
    """
    if not _available():
        return None
    try:
        bucket = _get_bucket()
        oss_key = f"images/{jid}.{suffix}"
        bucket.put_object(oss_key, image_bytes)
        return oss_key
    except Exception as e:
        print(f"[OSS] upload failed: {e}", flush=True)
        return None


def get_signed_url(oss_key: str, expires: int = 86400) -> str | None:
    """
    生成签名 URL（默认 24 小时有效）。
    用于返回给前端 crop_url。
    """
    if not oss_key or not _available():
        return None
    try:
        bucket = _get_bucket()
        return bucket.sign_url("GET", oss_key, expires)
    except Exception as e:
        print(f"[OSS] sign_url failed: {e}", flush=True)
        return None


def delete_object(oss_key: str) -> bool:
    """删除 OSS 对象。"""
    if not oss_key or not _available():
        return False
    try:
        bucket = _get_bucket()
        bucket.delete_object(oss_key)
        return True
    except Exception as e:
        print(f"[OSS] delete failed: {e}", flush=True)
        return False


def create_bucket_if_not_exists() -> bool:
    """创建 Bucket（如果不存在）。一次性操作。"""
    if not _available():
        return False
    try:
        import oss2
        _load_config()
        ak_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
        ak_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
        auth = oss2.Auth(ak_id, ak_secret)
        service = oss2.Service(auth, _endpoint)
        for b in service.list_buckets().buckets:
            if b.name == _bucket_name:
                print(f"[OSS] Bucket '{_bucket_name}' 已存在")
                return True
        # 创建 bucket
        service.create_bucket(
            oss2.models.BucketCreateConfig(
                _bucket_name,
                storage_class=oss2.BUCKET_STORAGE_CLASS_STANDARD,
            )
        )
        # 设置 7 天生命周期
        bucket = _get_bucket()
        rule = oss2.models.LifecycleRule(
            id="expire-7d",
            prefix="images/",
            status=oss2.models.LifecycleRule.ENABLED,
            expiration=oss2.models.LifecycleExpiration(days=7),
        )
        bucket.put_bucket_lifecycle(oss2.models.BucketLifecycle([rule]))
        print(f"[OSS] Bucket '{_bucket_name}' 已创建，7 天生命周期已设置")
        return True
    except Exception as e:
        print(f"[OSS] create_bucket failed: {e}", flush=True)
        return False
