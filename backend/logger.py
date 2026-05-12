"""
悠米伴学 — 结构化日志模块
轻量封装 Python logging，保持与 print() 兼容。
"""
import logging
import sys
import os
from datetime import datetime, timezone

_logger = None

def _get_logger():
    global _logger
    if _logger is not None:
        return _logger
    
    _logger = logging.getLogger("yomi")
    _logger.setLevel(logging.DEBUG)
    
    # 格式: [时间] [级别] [模块] trace_id=xxx msg
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] [%(module)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # stdout → systemd journal 可捕获
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    _logger.addHandler(handler)
    
    # 可选：文件日志（按日滚动）
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fh = logging.FileHandler(os.path.join(log_dir, f"backend-{today}.log"))
        fh.setFormatter(fmt)
        _logger.addHandler(fh)
    except Exception:
        pass  # 文件日志非关键，失败不影响
    
    return _logger


def log(level: str, msg: str, trace_id: str = "", **kwargs):
    """
    结构化日志入口。
    level: debug/info/warning/error
    trace_id: 链路 ID（worker 必须传）
    """
    logger = _get_logger()
    extra = f"trace_id={trace_id} " if trace_id else ""
    extra += " ".join(f"{k}={v}" for k, v in kwargs.items())
    full_msg = f"{extra}{msg}"
    getattr(logger, level)(full_msg)


# 便捷方法
def info(msg, trace_id="", **kwargs):
    log("info", msg, trace_id, **kwargs)

def warning(msg, trace_id="", **kwargs):
    log("warning", msg, trace_id, **kwargs)

def error(msg, trace_id="", **kwargs):
    log("error", msg, trace_id, **kwargs)

def debug(msg, trace_id="", **kwargs):
    log("debug", msg, trace_id, **kwargs)
