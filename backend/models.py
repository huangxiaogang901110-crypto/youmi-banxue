from __future__ import annotations

import uuid
from enum import Enum
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field


# ─── 枚举 ──────────────────────────────────────────────────

class EntitlementStatus(str, Enum):
    free_trial = "free_trial"
    member_active = "member_active"
    member_expired = "member_expired"
    credit_enough = "credit_enough"
    credit_low = "credit_low"
    credit_empty = "credit_empty"
    activation_mock_only = "activation_mock_only"


class JobStatus(str, Enum):
    created = "created"
    uploaded = "uploaded"
    enhancing = "enhancing"
    ocr_running = "ocr_running"
    cutting = "cutting"
    vision_reviewing = "vision_reviewing"
    schema_validating = "schema_validating"
    completed = "completed"
    needs_review = "needs_review"
    low_confidence = "low_confidence"
    failed = "failed"


class QuestionStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


# ─── 泛型响应 ──────────────────────────────────────────────

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    ok: bool = Field(True)
    data: Optional[T] = Field(None)
    code: Optional[str] = Field(None)
    message: Optional[str] = Field(None)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


# ─── 业务模型 ──────────────────────────────────────────────

class ParseJob(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.uploaded
    questions_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    file_name: Optional[str] = None
    image_url: Optional[str] = None


class Question(BaseModel):
    question_id: str
    question_number: int
    question_text: str
    kind: str = "question"
    question_role: Optional[str] = None
    context_text: Optional[str] = None
    options: Optional[List[str]] = None
    blank_count: Optional[int] = None
    bbox: Optional[List[float]] = None
    answer_bbox: Optional[List[float]] = None
    image_url: Optional[str] = None
    crop_url: Optional[str] = None
    visual_description: Optional[str] = None
    status: QuestionStatus = QuestionStatus.pending
    student_answer: Optional[str] = None
    is_correct: Optional[bool] = None
    grading_explanation: Optional[str] = None
    section_title: Optional[str] = None
    section_index: Optional[int] = None
    sub_index: Optional[int] = None
    source: Optional[str] = None
    confidence: Optional[float] = None
    error_code: Optional[str] = None


class TutorRequest(BaseModel):
    mode: str  # initial | followup
    message: str
    client_message_id: Optional[str] = None
    use_vision: Optional[bool] = False
    action: Optional[str] = None  # hint | step | solve


class TutorResponse(BaseModel):
    reply_text: str
    chat_limit_reached: bool = False
    remaining_rounds: int = 0
    credit_balance: int = -1  # -1 表示未返回（兼容旧版）
    request_id: Optional[str] = None


class Entitlement(BaseModel):
    user_id: str
    child_id: Optional[str] = None
    is_member: bool = False
    member_until: Optional[str] = None
    credit_balance: int = 0
    status: EntitlementStatus = EntitlementStatus.free_trial
