"""
全局内存状态单例。
所有路由和管线函数从这里 import，不直接访问 main.py 的全局变量。
"""
import db as _db
from models import Entitlement, Question, EntitlementStatus, QuestionStatus

# ── 启动时从 DB 加载 ──────────────────────────────────────────────
_jobs: dict[str, dict]
_model_calls: list[dict]
_tutor_chats: dict[str, list[dict]]
_credit_balances: dict[str, int]

_jobs, _model_calls, _tutor_chats, _credit_balances = _db.load_all()

# ── 429 延迟队列 ──────────────────────────────────────────────────
_deferred_vision_tasks: list = []

# ── 仅测试用 Mock 数据 ────────────────────────────────────────────
MOCK_ENTITLEMENT = Entitlement(
    user_id="p001", child_id="c001",
    is_member=False, credit_balance=50,
    status=EntitlementStatus.free_trial,
)

MOCK_QUESTIONS = [
    Question(
        question_id=f"q-{i:03d}",
        question_number=i,
        question_text=f"第{i}题：请计算 {i}×{i+1} 的结果。",
        bbox=[100, 50 + 60 * i, 400, 48],
        status=QuestionStatus.completed,
    )
    for i in range(1, 4)
]


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
