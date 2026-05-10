"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { entitlementApi } from "@/lib/api";
import type { ApiResponse, Entitlement } from "@/lib/types";
import { useEntitlementStore } from "@/stores/entitlementStore";

export function useEntitlement() {
  const { setFromApi, status, creditBalance, isMember, memberUntil } = useEntitlementStore();

  const query = useQuery({
    queryKey: ["entitlement"],
    queryFn: (): Promise<ApiResponse<Entitlement>> => entitlementApi.get(),
    staleTime: 30_000,
  });

  useEffect(() => {
    if (query.data?.ok && query.data.data) {
      setFromApi(query.data.data);
    }
  }, [query.data, setFromApi]);

  return {
    status,
    creditBalance,
    isMember,
    memberUntil,
    isLoading: query.isLoading,
    refetch: () => query.refetch(),
  };
}
