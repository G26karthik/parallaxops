"use client";

import { isTerminal, type JobState } from "@parallaxops/contracts";
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { API_URL, type Api, apiClient, unwrap } from "./api.ts";
import { useTenantId } from "./tenant.ts";

/** API client bound to this browser's tenant. `ready` is false until the tenant id is known. */
export function useApi(): { api: Api | null; tenantId: string | null } {
  const tenantId = useTenantId();
  const api = useMemo(() => (tenantId ? apiClient(tenantId) : null), [tenantId]);
  return { api, tenantId };
}

export function useJob(jobId: string) {
  const { api, tenantId } = useApi();
  return useQuery({
    queryKey: ["job", tenantId, jobId],
    enabled: !!api,
    queryFn: () => unwrap(api!.GET("/v1/jobs/{jobId}", { params: { path: { jobId } } })),
    // Poll while the job is alive; stop once it reaches a terminal state.
    refetchInterval: (q) => {
      const s = q.state.data?.state as JobState | undefined;
      return s && isTerminal(s) ? false : 1500;
    },
  });
}

export function useApiHealth() {
  return useQuery({
    queryKey: ["health"],
    refetchInterval: 15_000,
    queryFn: async () => {
      const r = await fetch(`${API_URL}/readyz`, { cache: "no-store" });
      const body = (await r.json()) as { status: string; components: Record<string, string> };
      return body;
    },
    retry: false,
  });
}
