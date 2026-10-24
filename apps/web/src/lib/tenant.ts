"use client";

import { useEffect, useState } from "react";

const KEY = "parallaxops.tenant";

/**
 * Per-browser tenant id. This is tenant *scoping*, not authentication (ADR 0004): it keeps one
 * browser's projects separate from another's on a shared deployment.
 */
const newId = () => `t-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
let sessionOnly: string | null = null;

export function getTenantId(): string {
  try {
    const existing = window.localStorage.getItem(KEY);
    if (existing) return existing;
    const id = newId();
    window.localStorage.setItem(KEY, id);
    return id;
  } catch {
    // Storage blocked (private mode): one id for this page load, shared by every caller. A new id
    // per call would give each component its own empty tenant.
    sessionOnly ??= newId();
    return sessionOnly;
  }
}

export function useTenantId(): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => setId(getTenantId()), []);
  return id;
}
