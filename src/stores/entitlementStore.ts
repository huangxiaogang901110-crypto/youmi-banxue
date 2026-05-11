"use client";

import { create } from "zustand";
import type { Entitlement, EntitlementStatus } from "@/lib/types";

interface EntitlementState {
  status: EntitlementStatus;
  creditBalance: number;
  isMember: boolean;
  memberUntil: string | null;
  setFromApi: (data: Entitlement) => void;
  setCreditBalance: (balance: number) => void;
  deductLocal: (amount: number) => void;
  addLocal: (amount: number) => void;
}

export const useEntitlementStore = create<EntitlementState>((set) => ({
  status: "free_trial",
  creditBalance: 50,
  isMember: false,
  memberUntil: null,

  setFromApi: (data) =>
    set({
      status: data.status,
      creditBalance: data.credit_balance,
      isMember: data.is_member,
      memberUntil: data.member_until ?? null,
    }),

  setCreditBalance: (balance) =>
    set((s) => ({
      creditBalance: balance,
      status: balance <= 0 ? "credit_empty" : s.status,
    })),

  deductLocal: (amount) =>
    set((s) => ({
      creditBalance: Math.max(0, s.creditBalance - amount),
      status: s.creditBalance - amount <= 0 ? "credit_empty" : s.status,
    })),

  addLocal: (amount) =>
    set((s) => ({
      creditBalance: s.creditBalance + amount,
      status: s.creditBalance + amount > 0 && s.status === "credit_empty" ? "free_trial" : s.status,
    })),
}));
