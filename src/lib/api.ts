import { JobStatus } from './types';
import type {
  ApiResponse,
  ParseJob,
  Question,
  TutorRequest,
  TutorResponse,
  Entitlement,
  AuthLoginResponse,
  AuthRegisterResponse,
  RecentJob,
} from './types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const MOCK_MODE = false;

// ─── Token 管理 ─────────────────────────────────────────────

const TOKEN_KEY = 'yomi_token';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * 统一的 JSON 请求封装，注入 Authorization 头部与超时处理。
 * timeoutMs 默认 30_000，登录/上传等慢接口应传更大值。
 */
async function typedFetch<T>(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = 30_000,
): Promise<ApiResponse<T>> {
  const t0 = performance.now();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    console.warn(`[typedFetch] timeout url=${url} timeoutMs=${timeoutMs} elapsed=${Math.round(performance.now() - t0)}ms`);
    controller.abort();
  }, timeoutMs);

  const headers = new Headers(options.headers);
  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  const token = getToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  try {
    const response = await fetch(url, { ...options, signal: controller.signal, headers });
    const body: ApiResponse<T> = await response.json();
    // 429 限流 — 后端返回友好 message，前端直接透传
    if (response.status === 429) {
      return { ok: false, code: body.code || 'rate_limited', message: body.message || '请求太频繁，请稍后再试', request_id: body.request_id || '' };
    }
    return body;
  } catch (error: unknown) {
    const isAbort = error instanceof DOMException && error.name === 'AbortError';
    const message = isAbort ? '请求超时，请稍后重试' :
      error instanceof Error ? error.message : '网络错误';
    return { ok: false, code: isAbort ? 'timeout' : 'network_error', message, request_id: '' };
  } finally {
    clearTimeout(timeoutId);
  }
}

/** 模拟网络延迟 */
function mockDelay(ms = 300): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── Mock 数据 ──────────────────────────────────────────────

const MOCK_JOB: ParseJob = {
  job_id: 'mock-job-001',
  status: JobStatus.UPLOADED,
  questions_count: 3,
  created_at: new Date().toISOString(),
  file_name: '数学作业.png',
};

const MOCK_QUESTIONS: Question[] = [
  {
    question_id: 'q-001',
    question_number: 1,
    question_text: '小明有12个苹果，吃了3个，又买了5个，还剩几个？',
    bbox: [120, 80, 400, 60],
    status: 'completed',
  },
  {
    question_id: 'q-002',
    question_number: 2,
    question_text: '一个长方形的长是8cm，宽是5cm，求它的面积。',
    bbox: [120, 180, 400, 60],
    visual_description: '几何图形：长方形，标注长8cm宽5cm',
    status: 'completed',
  },
  {
    question_id: 'q-003',
    question_number: 3,
    question_text: '计算：36 ÷ (4 + 2) × 3 = ?',
    bbox: [120, 280, 400, 60],
    status: 'completed',
  },
];

const MOCK_TUTOR_RESPONSE: TutorResponse = {
  reply_text:
    '我们一步步来看：\n\n1️⃣ 小明原来有12个苹果\n2️⃣ 吃了3个：12 - 3 = 9个\n3️⃣ 又买了5个：9 + 5 = 14个\n\n✅ 所以还剩14个苹果！',
  chat_limit_reached: false,
  remaining_rounds: 4,
  credit_balance: -1,
  request_id: 'mock-req-001',
};

const MOCK_ENTITLEMENT: Entitlement = {
  user_id: 'p001',
  child_id: 'c001',
  is_member: false,
  credit_balance: 50,
  status: 'free_trial',
};

// ─── Auth API ───────────────────────────────────────────────

export const authApi = {
  /** 登录：90s 超时，timeout/network error 自动重试 1 次 */
  login: async (phone: string, password: string): Promise<ApiResponse<AuthLoginResponse>> => {
    const tryLogin = () => typedFetch<AuthLoginResponse>(`${API_BASE_URL}/api/auth/login`, {
      method: 'POST',
      body: JSON.stringify({ phone, password }),
    }, 90_000);
    let res = await tryLogin();
    if (!res.ok && (res.code === 'timeout' || res.code === 'network_error')) {
      console.warn('[auth] login retry after first failure', res.code);
      await new Promise(r => setTimeout(r, 1000));
      res = await tryLogin();
    }
    return res;
  },

  register: (phone: string, password: string, name: string): Promise<ApiResponse<AuthRegisterResponse>> =>
    typedFetch<AuthRegisterResponse>(`${API_BASE_URL}/api/auth/register`, {
      method: 'POST',
      body: JSON.stringify({ phone, password, name }),
    }),

  children: (): Promise<ApiResponse<{ id: string; name: string; avatar?: string }[]>> =>
    typedFetch(`${API_BASE_URL}/api/auth/children`),
};

// ─── API 函数 ───────────────────────────────────────────────

/**
 * parseJobApi 提供试卷解析任务相关接口。
 */
export const parseJobApi = {
  /** 创建解析任务，上传文件 */
  async create(file: File, clientTaskId?: string, pageRange = "1"): Promise<ApiResponse<ParseJob>> {
    if (MOCK_MODE) {
      await mockDelay();
      const jobId = `job-${Date.now()}`;
      return { ok: true, data: { ...MOCK_JOB, job_id: jobId }, request_id: 'mock-req' };
    }
    const formData = new FormData();
    formData.append('file', file);
    if (clientTaskId) formData.append('client_task_id', clientTaskId);
    formData.append('page_range', pageRange);
    formData.append('source_type', 'web_upload');
    return typedFetch<ParseJob>(`${API_BASE_URL}/api/parse-jobs`, {
      method: 'POST',
      body: formData,
    }, 180_000);  // 上传大图片 + 慢网络，180s 超时
  },

  /** 获取解析任务状态 */
  getStatus: (jobId: string) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: { ...MOCK_JOB, job_id: jobId, status: JobStatus.OCR_RUNNING },
          request_id: 'mock-req',
        }))
      : typedFetch<ParseJob>(`${API_BASE_URL}/api/parse-jobs/${encodeURIComponent(jobId)}`),

  /** 获取解析出的题目列表 */
  getQuestions: (jobId: string) =>
    MOCK_MODE
      ? mockDelay().then(() => ({ ok: true as const, data: MOCK_QUESTIONS, request_id: 'mock-req' }))
      : typedFetch<Question[]>(`${API_BASE_URL}/api/parse-jobs/${encodeURIComponent(jobId)}/questions`),

  /** 获取当前 child 最近 10 条可展示的解析任务 */
  getRecent: () =>
    typedFetch<RecentJob[]>(`${API_BASE_URL}/api/parse-jobs/recent`),

  /** 软删除解析任务 */
  deleteJob: (jobId: string) =>
    typedFetch<{ job_id: string; deleted: boolean }>(`${API_BASE_URL}/api/parse-jobs/${encodeURIComponent(jobId)}`, {
      method: 'DELETE',
    }),

  /** 按 job_id 精确恢复任务状态（poll 失败后使用） */
  recoverByJobId: (jobId: string) =>
    typedFetch<{ job_id: string; status: string; questions_count: number; file_name: string }>(
      `${API_BASE_URL}/api/parse-jobs/${encodeURIComponent(jobId)}/recover`
    ),

  /** 按 client_upload_id 恢复超时上传的任务 */
  recoverByUploadId: (clientUploadId: string) =>
    typedFetch<{ job_id: string; status: string; questions_count: number; file_name: string }>(
      `${API_BASE_URL}/api/parse-jobs/recover?client_upload_id=${encodeURIComponent(clientUploadId)}`
    ),

  /** 两段式上传第一步：创建任务，立即返回 job_id */
  initJob: (params: { client_upload_id: string; file_name: string; file_size: number; mime_type: string }) =>
    typedFetch<{ job_id: string; status: string; file_name: string }>(`${API_BASE_URL}/api/parse-jobs/init`, {
      method: 'POST',
      body: JSON.stringify(params),
    }, 15_000),  // init 很快，15s 足够

  /** 两段式上传第二步：上传图片到已有任务 */
  uploadFileToJob: (jobId: string, file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    return typedFetch<{ job_id: string; status: string; file_name: string }>(
      `${API_BASE_URL}/api/parse-jobs/${encodeURIComponent(jobId)}/upload`, {
        method: 'POST',
        body: formData,
      }, 300_000  // 上传大图最多等 5 分钟
    );
  },
};

/**
 * questionApi 提供单题详情相关接口。
 */
export const questionApi = {
  getDetail: (questionId: string) =>
    MOCK_MODE
      ? mockDelay().then(() => {
          const q = MOCK_QUESTIONS.find((x) => x.question_id === questionId) || MOCK_QUESTIONS[0];
          return { ok: true as const, data: q, request_id: 'mock-req' };
        })
      : typedFetch<Question>(`${API_BASE_URL}/api/questions/${encodeURIComponent(questionId)}`),
};

/**
 * tutorApi 提供 AI 辅导相关接口。
 */
export const tutorApi = {
  send: (questionId: string, params: TutorRequest) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: {
            ...MOCK_TUTOR_RESPONSE,
            reply_text: params.mode === 'followup' ? '很好的追问！\n\n我们再仔细看看……' : MOCK_TUTOR_RESPONSE.reply_text,
          },
          request_id: 'mock-req',
        }))
      : typedFetch<TutorResponse>(`${API_BASE_URL}/api/questions/${encodeURIComponent(questionId)}/tutor`, {
          method: 'POST',
          body: JSON.stringify(params),
        }),
};

/**
 * visionApi 提供视觉二次路由接口。
 */
export const visionApi = {
  retry: (questionId: string) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: { reply_text: '（视觉重读结果）图中显示了一个长方形，标注了长和宽。', chat_limit_reached: false, remaining_rounds: 3 },
          request_id: 'mock-req',
        }))
      : typedFetch<TutorResponse>(`${API_BASE_URL}/api/questions/${encodeURIComponent(questionId)}/vision`, {
          method: 'POST',
        }),
};

/**
 * entitlementApi 提供权益查询接口。
 */
export const entitlementApi = {
  get: () =>
    MOCK_MODE
      ? mockDelay().then(() => ({ ok: true as const, data: MOCK_ENTITLEMENT, request_id: 'mock-req' }))
      : typedFetch<Entitlement>(`${API_BASE_URL}/api/me/entitlement`),
};

/**
 * activationApi 提供激活码兑换接口。
 */
export const activationApi = {
  redeem: (code: string) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: { activated: true, message: `激活码 ${code} 兑换成功`, credit_added: 100 },
          request_id: 'mock-req',
        }))
      : typedFetch<{ activated: boolean; message: string; credit_added?: number }>(
          `${API_BASE_URL}/api/activation/redeem`,
          { method: 'POST', body: JSON.stringify({ code }) }
        ),
};

/**
 * paymentApi 提供支付下单接口（Phase 0 占位）。
 */
export const paymentApi = {
  createOrder: (params: { plan_code: string; amount: number }) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: { order_id: 'mock-order-001', plan_code: params.plan_code, amount: params.amount, status: 'pending', payment_url: 'https://mock.pay.example.com' },
          request_id: 'mock-req',
        }))
      : typedFetch<{ order_id: string; status: string; payment_url?: string }>(
          `${API_BASE_URL}/api/payment/create-order`,
          { method: 'POST', body: JSON.stringify(params) }
        ),
};

/**
 * homeworkApi 提供微信群作业解析接口。
 * POST /api/homework/parse 将文本转为结构化任务清单。
 */
export const homeworkApi = {
  parse: (text: string) =>
    MOCK_MODE
      ? mockDelay().then(() => ({
          ok: true as const,
          data: mockParseHomework(text),
          request_id: "mock-req",
        }))
      : typedFetch<import("./types").HomeworkParseData>(
          `${API_BASE_URL}/api/homework/parse`,
          { method: "POST", body: JSON.stringify({ text }) }
        ),
};

function mockParseHomework(text: string): import("./types").HomeworkParseData {
  const subjects: import("./types").HomeworkSubject[] = [];
  const lines = text.split("\n").filter((l) => l.trim());

  let currentSubject: string | null = null;
  let inHomework = false;

  for (const line of lines) {
    // Skip Summary / non-homework sections
    if (/^(Summary|课堂内容|本节课)[：:]/i.test(line)) {
      inHomework = false;
      continue;
    }
    // Detect homework section
    if (/^(Homework|作业|任务)[：:]/i.test(line)) {
      inHomework = true;
      continue;
    }

    // Format A-C: 科目：任务
    const m1 = line.match(/^(?:【)?([\u4e00-\u9fff]{2,4})(?:】)?\s*[：:\-]\s*(.+)$/);
    const m2 = line.match(/^([\u4e00-\u9fff]{2,4})\s*-\s*(.+)$/);
    const m = m1 || m2;
    if (m) {
      const name = m[1];
      currentSubject = name;
      inHomework = true;
      const tasks = m[2].split(/[,，、/\-]\s*/).filter((t) => t.trim());
      const existing = subjects.find((s) => s.name === name);
      if (existing) {
        existing.tasks.push(...tasks);
      } else {
        subjects.push({ name, tasks: tasks.length ? tasks : [m[2].trim()] });
      }
      continue;
    }

    // Format D: 🌸1. task / ①task / 1. task
    if (inHomework) {
      let taskText = line
        .replace(/^[\uD83C-\uDBFF\uDC00-\uDFFF\u2600-\u27BF]*\s*\d+\s*[.、．)\s]+/, "")
        .replace(/^\d+\s*[.、．)]\s*/, "")
        .trim();
      if (taskText && taskText.length > 2) {
        const name = currentSubject || "作业";
        const existing = subjects.find((s) => s.name === name);
        if (existing) {
          existing.tasks.push(taskText);
        } else {
          subjects.push({ name, tasks: [taskText] });
        }
      }
      continue;
    }

    // Format E: plain task line after subject
    if (currentSubject && line.length > 3) {
      const existing = subjects.find((s) => s.name === currentSubject);
      if (existing) {
        existing.tasks.push(line);
      } else {
        subjects.push({ name: currentSubject, tasks: [line] });
      }
    }
  }

  return { subjects, raw_text: text };
}

// ─── 错题本 API ─────────────────────────────────────────────

export const mistakesApi = {
  list: (): Promise<ApiResponse<import("./types").MistakeItem[]>> =>
    typedFetch<import("./types").MistakeItem[]>(`${API_BASE_URL}/api/mistakes`),
  delete: (id: string): Promise<ApiResponse<{ id: string }>> =>
    typedFetch<{ id: string }>(`${API_BASE_URL}/api/mistakes/${id}`, { method: "DELETE" }),
  update: (id: string, body: { mastery_status?: string; error_type_code?: string; reason_desc?: string }): Promise<ApiResponse<{ id: string }>> =>
    typedFetch<{ id: string }>(`${API_BASE_URL}/api/mistakes/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
};

export const authSwitchApi = {
  switchChild: (childId: string): Promise<ApiResponse<{ token: string; active_child_id: string }>> =>
    typedFetch<{ token: string; active_child_id: string }>(`${API_BASE_URL}/api/auth/switch-child`, {
      method: "POST",
      body: JSON.stringify({ child_id: childId }),
    }),
};

// ─── 题目状态 API ───────────────────────────────────────────

export const questionStatusApi = {
  update: (questionId: string, status: string, childAnswer?: string): Promise<ApiResponse<{ question_id: string; status: string }>> =>
    typedFetch<{ question_id: string; status: string }>(`${API_BASE_URL}/api/questions/${encodeURIComponent(questionId)}/status`, {
      method: "POST",
      body: JSON.stringify({ status, child_answer: childAnswer || "" }),
    }),
};
