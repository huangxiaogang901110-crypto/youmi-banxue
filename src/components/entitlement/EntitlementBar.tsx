"use client";

import { useEntitlement } from "@/hooks/useEntitlement";
import type { EntitlementStatus } from "@/lib/types";

const statusLabel: Record<EntitlementStatus, string> = {
  free_trial: "内测体验中",
  member_active: "会员有效",
  member_expired: "会员已过期",
  credit_enough: "额度充足",
  credit_low: "额度偏低",
  credit_empty: "额度不足",
  activation_mock_only: "激活码验证",
};

const statusStyle: Record<EntitlementStatus, string> = {
  free_trial: "border-primary/30 text-primary bg-primary/10",
  member_active: "border-green-300 text-green-700 bg-green-50",
  member_expired: "border-amber-300 text-amber-700 bg-amber-50",
  credit_enough: "border-primary/30 text-primary bg-primary/10",
  credit_low: "border-amber-300 text-amber-700 bg-amber-50",
  credit_empty: "border-destructive/30 text-destructive bg-destructive/10",
  activation_mock_only: "border-primary/30 text-primary bg-primary/10",
};

export default function EntitlementBar() {
  const { status, creditBalance, isLoading } = useEntitlement();

  if (isLoading) {
    return <div className="h-7 w-20 bg-muted rounded-full animate-pulse" />;
  }

  return (
    <div className="flex items-center gap-2">
      <span className={`text-xs px-2.5 py-1 rounded-full border ${statusStyle[status]}`}>
        {statusLabel[status]}
      </span>
      <span className="text-xs text-muted-foreground">
        {creditBalance} 学豆
      </span>
    </div>
  );
}
