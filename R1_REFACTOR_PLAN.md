# 悠米作业批改系统重构 R1 — 实施方案

> 2026-05-22 | Hermes me | 开发区 `~/yomi-dev/`
> 状态：方案评审中，未动代码

---

## 一、数据模型变更

### 1.1 后端 `models.py` — Question 字段补齐

```python
class Question(BaseModel):
    question_id: str
    block_id: str = ""                 # ← 新增：所属大块ID
    question_number: int
    question_text: str
    child_answer: Optional[str] = None # ← 新增：孩子答案（替代 student_answer）
    standard_answer: Optional[str] = None  # ← 新增：标准答案
    is_correct: Optional[bool] = None
    subject: str = ""                  # ← 新增：math/chinese/english
    question_type: str = ""            # ← 新增：口算/竖式/选择/填空/连线/阅读/其他
    bbox: Optional[List[float]] = None     # 题区坐标 [x,y,w,h]
    answer_bbox: Optional[List[float]] = None  # ← 新增：答案区坐标
    confidence: float = 0.0            # ← 新增：识别置信度
    source: str = "qwen_vl"            # ← 新增：ocr/qwen_vl/bpp
    # 保留字段：
    crop_url: Optional[str] = None
    visual_description: Optional[str] = None
    status: QuestionStatus = QuestionStatus.pending
    grading_explanation: Optional[str] = None
    section_title: Optional[str] = None
    section_index: Optional[int] = None
    sub_index: Optional[int] = None
```

### 1.2 新增 `Block` 模型

```python
class Block(BaseModel):
    block_id: str
    job_id: str
    title: str = ""          # 大块标题：如 "一、口算练习"
    subject: str = ""        # math/chinese/english
    question_type: str = ""  # 口算/竖式/选择/填空等
    bbox: List[float] = []   # [x,y,w,h]
    question_ids: List[str] = []  # 包含的小题ID列表
    order: int = 0           # 排序序号
```

### 1.3 ParseJob 补字段

```python
class ParseJob(BaseModel):
    # 现有字段保留...
    reason_code: str = ""        # ← 新增
    subject: str = ""            # ← 新增
    blocks_count: int = 0        # ← 新增
    answer_count: int = 0        # ← 新增  
    graded_count: int = 0        # ← 新增
    confidence: float = 0.0      # ← 新增
    blocks: List[Block] = []     # ← 新增
```

### 1.4 前端 `types.ts` — Question 接口同步

```typescript
export interface Question {
  question_id: string;
  block_id: string;           // ← 新增
  question_number: number;
  question_text: string;
  child_answer?: string;      // ← 改名（原 student_answer）
  standard_answer?: string;   // ← 新增
  is_correct?: boolean | null;
  subject?: string;           // ← 新增
  question_type?: string;     // ← 新增
  bbox?: number[];
  answer_bbox?: number[];     // ← 新增
  confidence?: number;        // ← 新增
  source?: string;            // ← 新增
  crop_url?: string;
  visual_description?: string;
  status: QuestionStatus;
  grading_explanation?: string;
  section_title?: string;
  section_index?: number;
  sub_index?: number;
}

export interface Block {      // ← 新增
  block_id: string;
  title: string;
  subject: string;
  question_type: string;
  bbox: number[];
  question_ids: string[];
  order: number;
}
```

---

## 二、识别管线重构

### 当前流程（问题）
```
上传 → 图片压缩 → Qwen全图+OCR并行(10s SLA) 
  → Qwen成功? → 逐题建Question → grading → save
  → Qwen失败? → OCR→cut_to_questions → save → needs_review
  → B++灰度(当前关闭)
```

### 新流程
```
Phase 0: 图片质量检查（过暗/模糊/非作业）
Phase 1: OCR → blocks（已有）
Phase 2: 版面大块检测 → Block[]（新增）
Phase 3: 大块内切小题 → block内Question[]（重写question_cutter）
Phase 4: 小题答案区定位 → answer_bbox（新增）
Phase 5: Qwen逐块识别（裁块→Qwen→解析answer_bbox/child_answer/type）
Phase 6: 三科题型路由 → 规则/模型判题
Phase 7: 质量门 → status+reason_code → save
```

### 各Phase详细

**Phase 0: 图片质量检查**
- 亮度 < 80 → `rejected` / `reason_code=too_dark`
- 拉普拉斯方差 < 50 → `rejected` / `reason_code=too_blurry`
- OCR blocks < 3 且文本 < 20字 → `rejected` / `reason_code=empty_page`
- 非作业判断保留现有 `_is_homework_image(ocr_blocks)`

**Phase 1: OCR — 不变**
- `AliyunOCRClient().recognize()` → blocks[{text,x,y,w,h}]

**Phase 2: 版面大块检测（新模块 `block_detector.py`）**
- 输入：OCR blocks + 图像尺寸
- 输出：`Block[]`

检测规则：
```
1. 按Y坐标排序所有blocks
2. 检测大块分隔线：
   - 大标题行（字号大/居中/顶部）→ 新大块
   - 题型标签（"口算"/"竖式"/"选择"/"填空"/"阅读"）→ 新大块
   - 大空白间隔（Y间距 > 平均行高×3）→ 新大块
   - 题号大幅跳跃（如从5跳到15）→ 新大块
3. 语文特殊规则：课文标题/看拼音写词/阅读理解 各为独立大块
4. 数学特殊规则：口算密集区/竖式区/填空区/应用题 各为独立大块
5. 英语特殊规则：Review/Unit/选择/填空/连线/阅读 各为独立大块
6. 每个大块3-8小题或自然逻辑量
```

**Phase 3: 大块内切小题（重写 `question_cutter.py`）**

输入：Block.bbox 内的 OCR blocks
输出：`Question[]`（含 `question_text`, `bbox`, `block_id`）

切题规则：
```
1. 题号正则：^\s*(\d{1,3})[.、．)）\s]
2. 按视觉顺序排列（先Y后X）
3. 密集题按行/列组织（同行多题按X排序）
4. 同一block内连续题号视为同一大题
5. 每题的question_text = 该题号到下一题号之间所有blocks文本拼接
6. bbox = 该题所有blocks的union bbox
```

**Phase 4: 答案区定位（新模块 `answer_detector.py`）**

```
1. OCR blocks中找手写体关键字：数字/汉字/字母/符号密集区
2. 按题号对齐：每题下方/右侧的手写区
3. 通过Y坐标对比：问题区下方紧接的blocks
4. 简单规则：answer_bbox = [question_bbox.x, question_bbox.y+question_bbox.h, question_bbox.w, 平均行高×2]
5. Qwen裁图时以answer_bbox为指导缩小裁切范围
```

**Phase 5: Qwen逐块识别**

```
1. 不是整图识别，是按Block逐个裁图
2. 每个Block裁切→Qwen-VL→管道格式解析
3. Qwen prompt（每题一行）：
   "题号|题目内容|孩子答案|题型|置信度(0-1)"
   "无答案填「无」"
4. 解析后填入：child_answer, question_type, confidence
5. 失败块：保留OCR结果但标记source=ocr, confidence=0.3
6. 超时块：标记partial_recognition，已成功的块保留
```

**Phase 6: 三科判题路由**

```python
def route_grading(question: Question) -> Optional[bool]:
    if question.subject == "math":
        return math_grader.grade(question)  # 规则优先
    elif question.subject == "english":
        if question.standard_answer:
            return english_grader.grade(question)  # 有标准答案→判
        else:
            return None  # need_answer_key
    elif question.subject == "chinese":
        if question.question_type in ("填空", "选择", "看拼音"):
            return chinese_grader.grade(question)
        else:
            return None  # 主观题不判
```

**Phase 7: 质量门**

| 条件 | status | reason_code |
|------|--------|-------------|
| questions_count > 0 && answer_count > 0 | `completed` | — |
| questions_count > 0 && answer_count == 0 | `no_answers` | `no_child_answers` |
| 部分块成功/部分失败 | `partial_recognition` | `qwen_partial_timeout` / `ocr_partial` |
| 质量检查失败 | `rejected` | `too_dark` / `too_blurry` / `empty_page` |
| 非作业 | `rejected` | `non_homework` |
| OCR乱码>50% | `rejected` | `ocr_garbled` |
| 异常 | `failed` | 具体异常信息 |

---

## 三、状态机修复

| 状态 | 进入条件 | 前端行为 |
|------|---------|---------|
| `completed` | q>0 && answer>0 | 展示结果页（图上批改+切题模块） |
| `partial_recognition` | q>0，部分块失败 | 展示部分结果 + 提示"部分题目未识别成功" |
| `needs_review` | q>0，需人工确认 | 展示结果 + 警告"识别到题目但未识别到作答" |
| `no_answers` | q>0，answer=0 | 展示题目 + "未识别到作答，请重拍" |
| `rejected` | 质量门拒绝 | 显示 reason_code 对应文案 |
| `failed` | 异常 | "解析失败" |

**前端防空白兜底：**
1. completed 但 questions=[] → "识别结果为空，请重拍"
2. q=0/a=0 → 不进入正常结果页
3. needs_review 无题 → 显示错误原因

---

## 四、前端展示修复

### 4.1 图上批改（`BboxOverlay`改造）

```
规则：
1. is_correct=True → 在孩子答案右下角画绿色✓ SVG
2. is_correct=False → 红色○圈住孩子错误答案(answer_bbox)
3. answer_bbox无效(invalid/0) → 不画，不瞎画
4. 不用emoji
5. 不圈题干/页眉/老师批改/标题
```

### 4.2 切题模块（按 blocks 分组）

```
结构：
大块1：一、口算练习 (口算)
  ├─ 小题1: 3×5= □    答案: 15  ✓
  ├─ 小题2: 7+8= □    答案: 15  ✓
  └─ 小题3: 12-4= □   答案: 7   ✗
大块2：二、竖式计算 (竖式)
  ├─ 小题4: 45+38= □  答案: 83  ✓
  └─ 小题5: 91-27= □  答案: 64  ✓

每行小题：
- 对：绿色勾 SVG
- 错：红色叉 SVG  
- 未判：显示"待判"
- 点击进入小题详情（原题表达+孩子答案+对错）
```

### 4.3 状态文案映射

| reason_code | 用户文案 |
|-------------|---------|
| `too_dark` | "图片过暗，请在光线充足处重拍" |
| `too_blurry` | "图片模糊，请保持稳定后重拍" |
| `empty_page` | "未检测到作业内容，请重新拍摄" |
| `non_homework` | "未识别为作业图片，请拍摄作业页面" |
| `ocr_garbled` | "文字识别不清晰，请重新拍摄" |
| `no_child_answers` | "识别到题目但未识别到作答，请核对后重拍" |
| `qwen_partial_timeout` | "部分题目识别超时，已展示已完成部分" |

---

## 五、修改文件清单

### 后端（`~/yomi-dev/backend/`）

| 文件 | 改动 |
|------|------|
| `models.py` | Question加child_answer/answer_bbox/block_id/subject等；新增Block模型；JobStatus加partial_recognition/no_answers/rejected |
| `pipeline.py` | 重写worker_process_job：Phase 2-7新流程 |
| `question_cutter.py` | 重写：大块内切小题，按视觉顺序 |
| `block_detector.py` | **新增**：版面大块检测 |
| `answer_detector.py` | **新增**：答案区定位 |
| `math_grader.py` | 已有，需整合 |
| `chinese_grader.py` | **新增**：语文判题（填空/选择/看拼音） |
| `english_grader.py` | **新增**：英语判题（选择/填空/单词） |
| `parse_routes.py` | 返回blocks数据 + reason_code |

### 前端（`~/yomi-dev/src/`）

| 文件 | 改动 |
|------|------|
| `lib/types.ts` | Question加child_answer/answer_bbox/block_id/subject等；新增Block接口 |
| `app/workspace/page.tsx` | 图上批改改造；blocks分组展示；防空白兜底 |
| `hooks/useParseJobPolling.ts` | 适配新状态+新字段 |
| `components/question-list/QuestionGroup.tsx` | 适配新数据结构 |
| `components/bbox-overlay/` | 改造：红圈错题+绿勾对题 |

---

## 六、验证计划（20张）

| 分类 | 数量 | 验证要点 |
|------|:---:|---------|
| 数学 | 5 | 口算/竖式/填空/密集/混合，判对错+图上批改 |
| 语文 | 5 | 课文/填空/看拼音/阅读/密集文字 |
| 英语 | 5 | 选择/填空/单词/短句/Review |
| 异常 | 5 | 半页/模糊/过暗/非作业/空白 |

每张输出：job_id/耗时/status/reason_code/blocks_count/q_count/ans_count/bbox_count/answer_bbox_count/graded_count/图上批改是否显示/切题是否分块/小题详情是否准确/是否空白/是否0/0/0/是否误拒

---

## 七、验收标准

1. ✅ 正常作业不空白
2. ✅ 正常作业不0/0/0
3. ✅ 图上批改必须展示
4. ✅ 切题模块按大块分组
5. ✅ 小题详情有原题表达+孩子答案
6. ✅ 数学可判题显示对错
7. ✅ 英语/语文无答案时不瞎判但展示作答
8. ✅ 错题红圈答案，正确绿色勾
9. ✅ SVG不用emoji
10. ✅ 坏图明确拒绝
11. ✅ 20张全过
12. ❌ 不commit不push
