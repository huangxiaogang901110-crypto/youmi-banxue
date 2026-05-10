"use client";

import { useState } from "react";
import { X, Gift } from "lucide-react";
import { activationApi } from "@/lib/api";
import { useEntitlementStore } from "@/stores/entitlementStore";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ActivationModal({ open, onClose }: Props) {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const addLocal = useEntitlementStore((s) => s.addLocal);

  if (!open) return null;

  const handleRedeem = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");
    try {
      const resp = await activationApi.redeem(code.trim());
      if (!resp.ok || !resp.data) throw new Error("激活失败");
      if (resp.data.activated) {
        addLocal(resp.data.credit_added || 0);
        setSuccess(resp.data.message);
        setTimeout(() => { onClose(); setSuccess(""); setCode(""); }, 1500);
      } else {
        setError("激活码无效");
      }
    } catch {
      setError("激活失败，请检查激活码后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-card rounded-t-2xl sm:rounded-2xl w-full sm:max-w-sm p-6 space-y-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Gift className="w-5 h-5 text-primary" />
            <h3 className="text-foreground font-semibold">激活码兑换</h3>
          </div>
          <button onClick={onClose}><X className="w-5 h-5 text-muted-foreground" /></button>
        </div>

        {success ? (
          <p className="text-sm text-green-600 bg-green-50 rounded-xl p-3">{success}</p>
        ) : (
          <>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="输入激活码"
              className="w-full rounded-xl border border-border bg-background px-4 py-3 text-sm outline-none focus:border-primary/50 transition"
              onKeyDown={(e) => e.key === "Enter" && handleRedeem()}
            />
            {error && <p className="text-xs text-destructive">{error}</p>}
            <button
              onClick={handleRedeem}
              disabled={loading || !code.trim()}
              className="w-full rounded-xl bg-primary text-primary-foreground py-3 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
            >
              {loading ? "验证中…" : "兑换"}
            </button>
            <p className="text-xs text-muted-foreground text-center">内测阶段，激活码请联系客服获取</p>
          </>
        )}
      </div>
    </div>
  );
}
