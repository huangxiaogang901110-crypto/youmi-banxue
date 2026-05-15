"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { User, ChevronRight, LogOut, Trash2, RefreshCw } from "lucide-react";
import { authApi, authSwitchApi } from "@/lib/api";
import { getToken, setToken, clearToken } from "@/lib/api";
import { clearAllCache } from "@/lib/localCache";
import ErrorDisplay from "@/components/common/ErrorDisplay";

interface ChildInfo { id: string; name: string }

export default function ProfilePage() {
  const router = useRouter();
  const [children, setChildren] = useState<ChildInfo[]>([]);
  const [activeChildId, setActiveChildId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const resp = await authApi.children();
        if (resp.ok && resp.data) {
          setChildren(resp.data);
          const token = getToken();
          if (token) {
            try {
              const payload = JSON.parse(atob(token.split(".")[1]));
              setActiveChildId(payload.child_id || "");
            } catch {}
          }
        } else {
          setError(resp.message || "获取账号信息失败");
        }
      } catch {
        setError("网络错误，请稍后重试");
      }
    })();
  }, []);

  const handleSwitch = async (childId: string) => {
    setLoading(true);
    setError("");
    const resp = await authSwitchApi.switchChild(childId);
    if (resp.ok && resp.data) {
      setToken(resp.data.token);
      setActiveChildId(childId);
    } else {
      setError(resp.message || "切换失败");
    }
    setLoading(false);
  };

  const handleLogout = () => {
    clearToken();
    router.push("/login");
  };

  const handleClearCache = async () => {
    await clearAllCache();
    alert("本地缓存已清除");
  };

  const activeChild = children.find((c) => c.id === activeChildId);

  return (
    <div className="max-w-md mx-auto space-y-6 pb-4">
      {error && <ErrorDisplay message={error} onRetry={() => setError("")} />}

      {/* 当前孩子 */}
      <div className="bg-card rounded-2xl p-6 shadow-sm border border-border text-center space-y-2">
        <div className="w-16 h-16 bg-primary/10 rounded-full mx-auto flex items-center justify-center">
          <User className="w-8 h-8 text-primary" />
        </div>
        <div>
          <p className="text-lg font-semibold text-foreground">
            {activeChild?.name || "未选择"}
          </p>
          <p className="text-xs text-muted-foreground">当前学习账号</p>
        </div>
      </div>

      {/* 切换孩子 */}
      {children.length > 1 && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">切换孩子</p>
          {children.map((c) => (
            <button
              key={c.id}
              onClick={() => handleSwitch(c.id)}
              disabled={loading}
              className={`w-full flex items-center justify-between rounded-xl p-4 text-sm transition ${
                c.id === activeChildId
                  ? "bg-primary/10 border border-primary/20 text-primary font-medium"
                  : "bg-card border border-border text-foreground hover:bg-muted"
              }`}
            >
              <span>{c.name}</span>
              {c.id === activeChildId ? (
                <span className="text-xs text-primary">当前</span>
              ) : (
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              )}
            </button>
          ))}
        </div>
      )}

      {/* 操作 */}
      <div className="space-y-2">
        <button
          onClick={handleClearCache}
          className="w-full flex items-center gap-3 rounded-xl bg-card border border-border p-4 text-sm text-foreground hover:bg-muted transition"
        >
          <RefreshCw className="w-4 h-4 text-muted-foreground" />
          清除本地缓存
        </button>
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-3 rounded-xl bg-destructive/5 border border-destructive/20 p-4 text-sm text-destructive hover:bg-destructive/10 transition"
        >
          <LogOut className="w-4 h-4" />
          退出登录
        </button>
      </div>
    </div>
  );
}
