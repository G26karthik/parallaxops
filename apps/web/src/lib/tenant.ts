"use client";

import { useEffect, useState } from "react";

const KEY = "parallaxops.tenant";

/**
 * Per-browser tenant id. This is tenant *scoping*, not authentication (ADR 0004): it keeps one
 * browser's projects separate from another's on a shared deployment.
 */
export function getTenantId(): string {
  try {
    const existing = window.localStorage.getItem(KEY);
    if (existing) return existing;
    const id = `t-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
    window.localStorage.setItem(KEY, id);
    return id;
  } catch {
    // Storage blocked (private mode): fall back to a session-only id.
    return `t-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
  }
}

export function useTenantId(): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => setId(getTenantId()), []);
  return id;
}
