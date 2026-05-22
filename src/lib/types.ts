/**
 * 统一 API 响应格式
 * @template T 响应数据类型
 */
export interface ApiResponse<T> {
  /** 请求是否成功 */
  ok: boolean;
  /** 响应数据，仅在成功时返回 */
  data?: T;
  /** 错误码，用于标识具体错误类型 */
  code?: string;
  /** 错误或提示信息 */
  message?: string;
  /** 请求唯一标识，用于链路追踪 */
  request_id: string;
}

/**
 * 权益状态枚举
 */
export type EntitlementStatus =
  | 'free_trial'
  | 'member_active'
  | 'member_expired'
  | 'credit_enough'
  | 'credit_low'
  | 'credit_empty'
  | 'activation_mock_only';

/**
 * 作业解析任务状态枚举
 */
export enum JobStatus {
  UPLOADED = 'uploaded',
  ENHANCING = 'enhancing',
  OCR_RUNNING = 'ocr_running',
  CUTTING = 'cutting',
  VISION_REVIEWING = 'vision_reviewing',
  SCHEMA_VALIDATING = 'schema_validating',
  COMPLETED = 'completed',
  LOW_CONFIDENCE = 'low_confidence',
  NEEDS_REVIEW = 'needs_review',
  FAILED = 'failed',
}

/**
 * 题目处理状态
 */
export type QuestionStatus = 'pending' | 'completed' | 'failed';

/**
 * 作业解析任务接口
 */
export interface ParseJob {
  /** 作业任务唯一标识 */
  job_id: string;
  /** 当前任务状态 */
  status: JobStatus;
  /** 识别出的题目数量 */
  questions_count: number;
  /** 任务创建时间（ISO字符串） */
  created_at?: string;
  /** 任务最后更新时间（ISO字符串） */
  updated_at?: string;
  /** 原始作业文件名 */
  file_name?: string;
  /** 作业总页数 */
  pages_count?: number;
  /** 拒绝/失败原因码 */
  reason_code?: string;
  /** 科目 */
  subject?: string;
  /** 大块数量 */
  blocks_count?: number;
  /** 有答案的题数 */
  answer_count?: number;
  /** 已判对错题数 */
  graded_count?: number;
  /** 整体置信度 */
  confidence?: number;
}

/**
 * 题目接口
 */
export interface Question {
  /** 题目唯一标识 */
  question_id: string;
  /** 所属大块ID */
  block_id?: string;
  /** 题目在作业中的序号 */
  question_number: number;
  /** 题目文本内容 */
  question_text: string;
  /** 孩子答案 */
  child_answer?: string | null;
  /** 标准答案 */
  standard_answer?: string | null;
  /** DeepSeek 判对错结果 */
  is_correct?: boolean | null;
  /** 科目 */
  subject?: string;
  /** 题型 */
  question_type?: string;
  /** 边界框坐标，格式如 [x, y, width, height] */
  bbox?: number[];
  /** 答案区边界框坐标 */
  answer_bbox?: number[];
  /** 识别置信度 */
  confidence?: number;
  /** 识别来源 */
  source?: string;
  /** 题目区域截图URL */
  crop_url?: string;
  /** 题目视觉描述，用于辅助讲解 */
  visual_description?: string;
  /** 题目处理状态 */
  status: QuestionStatus;
  /** @deprecated 孩子手写/填写的答案，使用 child_answer */
  student_answer?: string | null;
  /** DeepSeek 判题简短解释 */
  grading_explanation?: string | null;
  /** 大题/题组标题 */
  section_title?: string | null;
  /** 大题序号（从1开始） */
  section_index?: number | null;
  /** 大题内小题序号（从1开始） */
  sub_index?: number | null;
}

/**
 * 大块接口
 */
export interface Block {
  block_id: string;
  job_id?: string;
  title: string;
  subject: string;
  question_type: string;
  bbox: number[];
  question_ids: string[];
  order: number;
}

/**
 * 辅导对话请求
 */
export interface TutorRequest {
  /** 对话模式：初始提问或追问 */
  mode: 'initial' | 'followup';
  /** 用户发送的消息 */
  message: string;
  /** 辅导行为模式：hint/step/solve */
  action?: 'hint' | 'step' | 'solve';
  /** 客户端消息幂等ID，用于去重 */
  client_message_id?: string;
  /** 是否启用视觉理解能力 */
  use_vision?: boolean;
}

/**
 * 辅导对话响应
 */
export interface TutorResponse {
  /** 助手回复文本 */
  reply_text: string;
  /** 是否已达到本轮对话上限 */
  chat_limit_reached: boolean;
  /** 本轮剩余可对话轮数 */
  remaining_rounds: number;
  /** 调用后剩余学豆余额（-1 表示未返回，兼容旧版） */
  credit_balance: number;
  /** 请求追踪ID */
  request_id?: string;
}

/**
 * 用户权益信息
 */
export interface Entitlement {
  /** 用户唯一标识 */
  user_id: string;
  /** 子账号ID，面向家长场景可选 */
  child_id?: string;
  /** 是否为有效会员 */
  is_member: boolean;
  /** 会员到期时间，ISO日期字符串 */
  member_until?: string;
  /** 当前剩余积分数（用于非会员按次消耗） */
  credit_balance: number;
  /** 权益状态 */
  status: EntitlementStatus;
}

/** 作业科目 + 任务列表（微信群作业解析结果） */
export interface HomeworkSubject {
  /** 科目名称（语文/数学/英语等） */
  name: string;
  /** 该科目的任务列表 */
  tasks: string[];
}

/** POST /api/homework/parse 响应 */
export interface HomeworkParseData {
  subjects: HomeworkSubject[];
  raw_text: string;
}

// ─── Auth 类型 ──────────────────────────────────────────────

export interface AuthLoginResponse {
  token: string;
  parent: { id: string; name: string; phone: string };
  children: { id: string; name: string }[];
  active_child_id: string;
}

export interface AuthRegisterResponse {
  token: string;
  parent: { id: string; name: string; phone: string };
  children: { id: string; name: string }[];
  active_child_id: string;
}

// ─── 错题本类型 ──────────────────────────────────────────────

export interface MistakeItem {
  id: string;
  child_id: string;
  question_id: string;
  error_type_code: string;
  reason_desc: string;
  mastery_status: string;
  next_review_at?: string;
  created_at?: string;
  question_text?: string;
  crop_url?: string;
  visual_description?: string;
}

export interface RecentJob {
  job_id: string;
  status: string;
  questions_count: number;
  file_name: string;
  created_at: string;
}
