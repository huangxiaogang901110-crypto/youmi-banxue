"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { mistakesApi } from "@/lib/api";
import type { MistakeItem } from "@/lib/types";
import { SUBJECT_TREE, classifyMistake, type ClassifyResult } from "@/lib/mistakeClassifier";

const ERROR_LABELS: Record<string, string> = {
  calculation_error: "计算错误",
  conceptual_error: "概念不清",
  careless_mistake: "粗心",
  reading_error: "审题失误",
  unknown: "未分类",
};

const ERROR_COLORS: Record<string, string> = {
  calculation_error: "bg-rose-50 text-rose-600",
  conceptual_error: "bg-amber-50 text-amber-600",
  careless_mistake: "bg-sky-50 text-sky-600",
  reading_error: "bg-violet-50 text-violet-600",
  unknown: "bg-slate-100 text-slate-500",
};

interface MistakeWithClass extends MistakeItem {
  classify: ClassifyResult;
}

// ── 构建分类树（只含非空节点） ──
function buildTree(items: MistakeWithClass[]) {
  const bySubject = new Map<string, Map<string, Map<string, MistakeWithClass[]>>>();

  for (const item of items) {
    const { subject, domain, knowledge } = item.classify;
    if (!bySubject.has(subject)) bySubject.set(subject, new Map());
    const domMap = bySubject.get(subject)!;
    if (!domMap.has(domain)) domMap.set(domain, new Map());
    const kpMap = domMap.get(domain)!;
    if (!kpMap.has(knowledge)) kpMap.set(knowledge, []);
    kpMap.get(knowledge)!.push(item);
  }

  return bySubject;
}

// ── 错误类型颜色 ──
const SUBJECT_COLORS: Record<string, string> = {
  "数学": "bg-blue-50 text-blue-700",
  "语文": "bg-emerald-50 text-emerald-700",
  "英语": "bg-violet-50 text-violet-700",
  "未分类": "bg-slate-100 text-slate-500",
};

const DOMAIN_COLORS: Record<string, string> = {
  "数学": "bg-blue-50/50 text-blue-600",
  "语文": "bg-emerald-50/50 text-emerald-600",
  "英语": "bg-violet-50/50 text-violet-600",
  "未分类": "bg-slate-100/50 text-slate-500",
};

export default function MistakesPage() {
  const [items, setItems] = useState<MistakeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [removing, setRemoving] = useState<Set<string>>(new Set());
  const router = useRouter();

  // 展开状态：按 "subject" / "subject|domain" / "subject|domain|knowledge" 三级
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    (async () => {
      try {
        const resp = await mistakesApi.list();
        if (resp.ok && resp.data) setItems(resp.data);
      } catch {}
      setLoading(false);
    })();
  }, []);

  // 归类
  const classified = useMemo<MistakeWithClass[]>(() => {
    return items.map((item) => ({
      ...item,
      classify: classifyMistake(item.question_text),
    }));
  }, [items]);

  // 构建非空分类树
  const tree = useMemo(() => buildTree(classified), [classified]);

  const toggle = (key: string) => {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setExpanded(next);
  };

  const handleRemove = async (e: React.MouseEvent, item: MistakeItem) => {
    e.stopPropagation();
    if (removing.has(item.id)) return;
    const nextRemoving = new Set(removing);
    nextRemoving.add(item.id);
    setRemoving(nextRemoving);
    try {
      await mistakesApi.delete(item.id);
      try {
        const stored = localStorage.getItem("yomi_mistake_ids");
        if (stored) {
          const ids: string[] = JSON.parse(stored);
          const updated = ids.filter((qid) => qid !== item.question_id);
          localStorage.setItem("yomi_mistake_ids", JSON.stringify(updated));
        }
      } catch {}
      setItems((prev) => prev.filter((i) => i.id !== item.id));
    } catch {}
    const next = new Set(removing);
    next.delete(item.id);
    setRemoving(next);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="space-y-4 pb-4">
        <h1 className="text-lg font-bold text-foreground">错题本</h1>
        <div className="bg-card rounded-2xl p-10 shadow-sm border border-border text-center space-y-3">
          <BookOpen className="w-10 h-10 text-muted-foreground mx-auto" />
          <p className="text-muted-foreground text-sm">还没有错题记录</p>
          <p className="text-xs text-muted-foreground">辅导后点击「加入错题」即可记录</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 pb-4">
      <h1 className="text-lg font-bold text-foreground">错题本</h1>

      {/* ── 一级：学科 ── */}
      {SUBJECT_TREE.map((subjectNode) => {
        const subKey = subjectNode.label;
        const domMap = tree.get(subKey);
        if (!domMap || domMap.size === 0) return null; // 0 道 → 不展示

        const totalInSubject = [...domMap.values()].reduce(
          (sum, kpMap) => sum + [...kpMap.values()].reduce((s, arr) => s + arr.length, 0),
          0,
        );
        const isSubExpanded = expanded.has(subKey);

        return (
          <div key={subKey} className="bg-card rounded-xl shadow-sm border border-border overflow-hidden">
            {/* 学科头 */}
            <button
              onClick={() => toggle(subKey)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-muted/20 transition"
            >
              <div className="flex items-center gap-2.5">
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${SUBJECT_COLORS[subKey] || "bg-slate-100 text-slate-600"}`}>
                  {subKey}
                </span>
                <span className="text-xs text-muted-foreground">{totalInSubject} 道</span>
              </div>
              {isSubExpanded ? (
                <ChevronDown className="w-4 h-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              )}
            </button>

            {/* ── 二级：知识域 ── */}
            {isSubExpanded &&
              (subjectNode.children || []).map((domainNode) => {
                const domKey = `${subKey}|${domainNode.label}`;
                const kpMap = domMap.get(domainNode.label);
                if (!kpMap || kpMap.size === 0) return null; // 0 道 → 不展示

                const totalInDomain = [...kpMap.values()].reduce((s, arr) => s + arr.length, 0);
                const isDomExpanded = expanded.has(domKey);

                return (
                  <div key={domKey} className="border-t border-border/50">
                    {/* 知识域头 */}
                    <button
                      onClick={() => toggle(domKey)}
                      className="w-full flex items-center justify-between px-4 py-2.5 pl-8 hover:bg-muted/20 transition"
                    >
                      <div className="flex items-center gap-2">
                        <span className={`text-[11px] px-1.5 py-0.5 rounded-full ${DOMAIN_COLORS[subKey] || "bg-slate-100 text-slate-500"}`}>
                          {domainNode.label}
                        </span>
                        <span className="text-[11px] text-muted-foreground">{totalInDomain} 道</span>
                      </div>
                      {isDomExpanded ? (
                        <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                      )}
                    </button>

                    {/* ── 三级：知识点 + 错题卡片 ── */}
                    {isDomExpanded &&
                      (domainNode.children || []).map((kpNode) => {
                        const kpKey = `${domKey}|${kpNode.label}`;
                        const mistakeList = kpMap.get(kpNode.label);
                        if (!mistakeList || mistakeList.length === 0) return null;

                        const isKpExpanded = expanded.has(kpKey);

                        return (
                          <div key={kpKey} className="border-t border-border/30">
                            {/* 知识点头 */}
                            <button
                              onClick={() => toggle(kpKey)}
                              className="w-full flex items-center justify-between px-4 py-2 pl-12 hover:bg-muted/20 transition"
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-foreground font-medium">
                                  {kpNode.label}
                                </span>
                                <span className="text-[11px] text-muted-foreground">
                                  {mistakeList.length} 道
                                </span>
                              </div>
                              {isKpExpanded ? (
                                <ChevronDown className="w-3 h-3 text-muted-foreground" />
                              ) : (
                                <ChevronRight className="w-3 h-3 text-muted-foreground" />
                              )}
                            </button>

                            {/* 错题卡片列表 */}
                            {isKpExpanded && (
                              <div className="divide-y divide-border/30 border-t border-border/30">
                                {mistakeList.map((item) => (
                                  <div
                                    key={item.id}
                                    className="px-4 py-2.5 pl-16 flex items-center gap-2.5 hover:bg-muted/20 transition cursor-pointer"
                                    onClick={() => router.push(`/question?id=${item.question_id}`)}
                                  >
                                    {/* Thumbnail */}
                                    {item.crop_url ? (
                                      <img
                                        src={item.crop_url}
                                        alt=""
                                        className="w-8 h-8 rounded-md object-cover bg-muted shrink-0"
                                        onError={(e) => {
                                          (e.target as HTMLImageElement).style.display = "none";
                                        }}
                                      />
                                    ) : null}

                                    {/* Content */}
                                    <div className="flex-1 min-w-0">
                                      <p className="text-xs text-foreground line-clamp-1 leading-snug">
                                        {item.question_text ||
                                          item.visual_description ||
                                          `题目 ${item.question_id?.slice(-6) || ""}`}
                                      </p>
                                      <div className="flex items-center gap-1.5 mt-0.5">
                                        <span
                                          className={`text-[10px] px-1 py-0 rounded-full ${
                                            ERROR_COLORS[item.error_type_code] || ERROR_COLORS.unknown
                                          }`}
                                        >
                                          {ERROR_LABELS[item.error_type_code] || item.error_type_code}
                                        </span>
                                        <span
                                          className={`text-[10px] px-1 py-0 rounded-full ${
                                            item.mastery_status === "mastered"
                                              ? "bg-green-50 text-green-600"
                                              : "bg-amber-50 text-amber-600"
                                          }`}
                                        >
                                          {item.mastery_status === "mastered" ? "已掌握" : "待复习"}
                                        </span>
                                      </div>
                                    </div>

                                    {/* Remove */}
                                    <button
                                      onClick={(e) => handleRemove(e, item)}
                                      disabled={removing.has(item.id)}
                                      className="shrink-0 p-1.5 rounded-lg text-muted-foreground hover:text-red-500 hover:bg-red-50 transition disabled:opacity-50"
                                    >
                                      {removing.has(item.id) ? (
                                        <div className="w-3.5 h-3.5 border-2 border-red-400 border-t-transparent rounded-full animate-spin" />
                                      ) : (
                                        <Trash2 className="w-3.5 h-3.5" />
                                      )}
                                    </button>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                  </div>
                );
              })}
          </div>
        );
      })}
    </div>
  );
}
