// 错题库多级分类框架 — 教育部小学1-6年级教学大纲概括
// 学科 → 知识域 → 知识点 → 具体错题

export interface KnowledgeNode {
  label: string;
  children?: KnowledgeNode[];
}

export const SUBJECT_TREE: KnowledgeNode[] = [
  {
    label: "数学",
    children: [
      {
        label: "数与运算",
        children: [
          { label: "20以内加减法" },
          { label: "100以内加减法" },
          { label: "表内乘除法" },
          { label: "万以内加减法" },
          { label: "多位数乘除法" },
          { label: "小数加减乘除" },
          { label: "分数加减乘除" },
          { label: "四则混合运算" },
          { label: "其他" },
        ],
      },
      {
        label: "图形与几何",
        children: [
          { label: "认识图形" },
          { label: "长度面积体积" },
          { label: "角度与三角形" },
          { label: "圆与圆柱圆锥" },
          { label: "其他" },
        ],
      },
      {
        label: "量与单位",
        children: [
          { label: "元角分与时分秒" },
          { label: "千米吨年月日" },
          { label: "公顷与体积单位" },
          { label: "其他" },
        ],
      },
      {
        label: "应用题与思维",
        children: [
          { label: "一步应用题" },
          { label: "两步应用题" },
          { label: "多步复合应用题" },
          { label: "找规律与推理" },
          { label: "其他" },
        ],
      },
    ],
  },
  {
    label: "语文",
    children: [
      {
        label: "识字与写字",
        children: [
          { label: "拼音与笔画" },
          { label: "偏旁部首与查字典" },
          { label: "多音字与形近字" },
          { label: "其他" },
        ],
      },
      {
        label: "词语与成语",
        children: [
          { label: "近义词与反义词" },
          { label: "成语与谚语" },
          { label: "词语搭配与辨析" },
          { label: "其他" },
        ],
      },
      {
        label: "句子与标点",
        children: [
          { label: "把字句与被字句" },
          { label: "缩句扩句修改病句" },
          { label: "修辞手法" },
          { label: "标点符号" },
          { label: "其他" },
        ],
      },
      {
        label: "阅读理解",
        children: [
          { label: "课内阅读" },
          { label: "课外短文阅读" },
          { label: "文言文入门" },
          { label: "其他" },
        ],
      },
      {
        label: "写作",
        children: [
          { label: "看图写话" },
          { label: "记叙文" },
          { label: "说明文与议论文" },
          { label: "其他" },
        ],
      },
    ],
  },
  {
    label: "英语",
    children: [
      {
        label: "词汇",
        children: [
          { label: "名词动词形容词" },
          { label: "数字颜色动物食物" },
          { label: "抽象词汇与短语" },
          { label: "其他" },
        ],
      },
      {
        label: "句型与语法",
        children: [
          { label: "There be与一般现在时" },
          { label: "现在进行时与过去时" },
          { label: "一般将来时与比较级" },
          { label: "其他" },
        ],
      },
      {
        label: "听力与口语",
        children: [
          { label: "课堂指令与问候" },
          { label: "日常对话与短文" },
          { label: "其他" },
        ],
      },
      {
        label: "阅读与写作",
        children: [
          { label: "看图读句" },
          { label: "短文阅读与仿写" },
          { label: "其他" },
        ],
      },
    ],
  },
];

// ─── 关键词推断规则 ──────────────────────────────────

interface Rule {
  subject: string;
  domain: string;
  knowledge: string;
  keywords: string[];
}

const CLASSIFY_RULES: Rule[] = [
  // ── 语文 — 优先级最高（含中文特征词） ──
  { subject: "语文", domain: "识字与写字", knowledge: "拼音与笔画", keywords: ["拼音", "声母", "韵母", "笔画", "笔顺", "田字格"] },
  { subject: "语文", domain: "识字与写字", knowledge: "偏旁部首与查字典", keywords: ["偏旁", "部首", "查字典", "音序", "部首查字"] },
  { subject: "语文", domain: "识字与写字", knowledge: "多音字与形近字", keywords: ["多音字", "形近字", "同音字", "错别字"] },
  { subject: "语文", domain: "词语与成语", knowledge: "近义词与反义词", keywords: ["近义词", "反义词", "同义词"] },
  { subject: "语文", domain: "词语与成语", knowledge: "成语与谚语", keywords: ["成语", "谚语", "歇后语", "寓言"] },
  { subject: "语文", domain: "词语与成语", knowledge: "词语搭配与辨析", keywords: ["词语搭配", "选词", "填空"] },
  { subject: "语文", domain: "句子与标点", knowledge: "把字句与被字句", keywords: ["把字句", "被字句", "改为把", "改为被"] },
  { subject: "语文", domain: "句子与标点", knowledge: "缩句扩句修改病句", keywords: ["缩句", "扩句", "病句", "修改病句"] },
  { subject: "语文", domain: "句子与标点", knowledge: "修辞手法", keywords: ["比喻", "拟人", "排比", "夸张", "修辞"] },
  { subject: "语文", domain: "句子与标点", knowledge: "标点符号", keywords: ["标点", "逗号", "句号", "问号", "感叹号", "冒号", "引号"] },
  { subject: "语文", domain: "阅读理解", knowledge: "课内阅读", keywords: ["课文", "根据课文", "背诵"] },
  { subject: "语文", domain: "阅读理解", knowledge: "课外短文阅读", keywords: ["阅读短文", "阅读下面", "短文"] },
  { subject: "语文", domain: "阅读理解", knowledge: "文言文入门", keywords: ["文言文", "之乎者也", "古诗", "注释"] },
  { subject: "语文", domain: "写作", knowledge: "看图写话", keywords: ["看图写话", "看图说话"] },
  { subject: "语文", domain: "写作", knowledge: "记叙文", keywords: ["作文", "记叙", "日记", "周记"] },
  { subject: "语文", domain: "写作", knowledge: "说明文与议论文", keywords: ["说明文", "议论文", "说明方法"] },

  // ── 数学 — 算式符号精确匹配 ──
  { subject: "数学", domain: "数与运算", knowledge: "20以内加减法", keywords: ["1+", "2+", "3+", "4+", "5+", "6+", "7+", "8+", "9+", "10+"] },
  { subject: "数学", domain: "数与运算", knowledge: "100以内加减法", keywords: ["12+", "23+", "34+", "45+", "56+", "67+", "78+", "89+", "91+"] },
  { subject: "数学", domain: "数与运算", knowledge: "表内乘除法", keywords: ["×", "÷", "乘法", "除法", "口诀"] },
  { subject: "数学", domain: "数与运算", knowledge: "小数加减乘除", keywords: ["小数", "0.", "小数点"] },
  { subject: "数学", domain: "数与运算", knowledge: "分数加减乘除", keywords: ["分数", "几分之", "分母", "分子", "约分", "通分"] },
  { subject: "数学", domain: "数与运算", knowledge: "四则混合运算", keywords: ["脱式", "简便", "运算律", "混合运算"] },
  { subject: "数学", domain: "图形与几何", knowledge: "认识图形", keywords: ["正方形", "长方形", "三角形", "圆形", "图形名称"] },
  { subject: "数学", domain: "图形与几何", knowledge: "长度面积体积", keywords: ["厘米", "米", "千米", "面积", "周长", "体积", "平方"] },
  { subject: "数学", domain: "图形与几何", knowledge: "角度与三角形", keywords: ["角度", "锐角", "钝角", "直角", "等腰", "等边"] },
  { subject: "数学", domain: "图形与几何", knowledge: "圆与圆柱圆锥", keywords: ["半径", "直径", "圆周", "圆柱", "圆锥", "表面积"] },
  { subject: "数学", domain: "量与单位", knowledge: "元角分与时分秒", keywords: ["元", "角", "分", "时分", "秒", "钟表"] },
  { subject: "数学", domain: "量与单位", knowledge: "千米吨年月日", keywords: ["千米", "吨", "克", "千克", "年月日", "闰年"] },
  { subject: "数学", domain: "应用题与思维", knowledge: "找规律与推理", keywords: ["找规律", "推理", "鸡兔同笼", "植树问题", "排列"] },

  // ── 英语 — 英文特征词 ──
  { subject: "英语", domain: "句型与语法", knowledge: "There be与一般现在时", keywords: ["There is", "There are", "there is", "there are"] },
  { subject: "英语", domain: "句型与语法", knowledge: "现在进行时与过去时", keywords: ["is reading", "are playing", "was", "were", "yesterday"] },
  { subject: "英语", domain: "句型与语法", knowledge: "一般将来时与比较级", keywords: ["will", "going to", "taller", "bigger", "better"] },
  { subject: "英语", domain: "词汇", knowledge: "数字颜色动物食物", keywords: ["apple", "banana", "dog", "cat", "red", "blue", "one", "two"] },
];

// ─── 辅助函数 ──────────────────────────────────

/** 从文本中提取所有整数 */
function extractNumbers(text: string): number[] {
  const matches = text.match(/\d+/g);
  if (!matches) return [];
  return matches.map(Number).filter(n => n > 0 && n <= 999);
}

// ─── 推断函数 ──────────────────────────────────

export interface ClassifyResult {
  subject: string;    // 学科：数学/语文/英语
  domain: string;     // 知识域
  knowledge: string;  // 知识点
}

export function classifyMistake(text: string | undefined | null): ClassifyResult {
  const t = (text || "").trim().toLowerCase();

  // 1. 关键词精确匹配
  for (const rule of CLASSIFY_RULES) {
    for (const kw of rule.keywords) {
      if (t.includes(kw.toLowerCase())) {
        // ── 数与运算域 加减法 → 数值范围二次分流 ──
        if (rule.knowledge === "20以内加减法" || rule.knowledge === "100以内加减法") {
          const numbers = extractNumbers(t);
          const maxNum = numbers.length > 0 ? Math.max(...numbers) : 0;

          if (maxNum > 0 && maxNum <= 20) {
            return { subject: "数学", domain: "数与运算", knowledge: "20以内加减法" };
          }
          if (maxNum >= 21 && maxNum <= 100) {
            return { subject: "数学", domain: "数与运算", knowledge: "100以内加减法" };
          }
        }
        return { subject: rule.subject, domain: rule.domain, knowledge: rule.knowledge };
      }
    }
  }

  // 2. 简单数学运算符号匹配（>=3个数字/符号 → 数学）
  const mathChars = (t.match(/[+\-×÷=0-9]/g) || []).length;
  if (mathChars >= 3) {
    // ── 数值范围分流 ──
    const numbers = extractNumbers(t);
    const maxNum = numbers.length > 0 ? Math.max(...numbers) : 0;

    if (maxNum > 0 && maxNum <= 20) {
      return { subject: "数学", domain: "数与运算", knowledge: "20以内加减法" };
    }
    if (maxNum >= 21 && maxNum <= 100) {
      return { subject: "数学", domain: "数与运算", knowledge: "100以内加减法" };
    }
    return { subject: "数学", domain: "数与运算", knowledge: "其他" };
  }

  // 3. 英文字母占比 >50% → 英语
  const alphaCount = (t.match(/[a-zA-Z]/g) || []).length;
  if (t.length > 0 && alphaCount / t.length > 0.5) {
    return { subject: "英语", domain: "其他", knowledge: "其他" };
  }

  // 4. 含中文字符 → 语文
  if (/[\u4e00-\u9fff]/.test(t)) {
    return { subject: "语文", domain: "其他", knowledge: "其他" };
  }

  // 5. 兜底
  return { subject: "语文", domain: "其他", knowledge: "其他" };
}
