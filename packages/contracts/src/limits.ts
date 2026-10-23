import { z } from "zod";

export const LIMITS = {
  maxImageBytes: 40 * 1024 * 1024,
  maxFilesPerDataset: 80,
  maxDatasetBytes: 1_200_000_000,
  maxFilenameLength: 120,
  presignTtlSeconds: 900,
  maxJobsQueuedPerTenant: 5,
  maxActiveJobsPerTenant: 2,
} as const;

export const ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png"] as const;
export const EXTENSION_BY_TYPE: Record<(typeof ALLOWED_CONTENT_TYPES)[number], string> = {
  "image/jpeg": "jpg",
  "image/png": "png",
};

/** Magic bytes checked server-side against the first bytes of the uploaded object. */
export const MAGIC: Record<(typeof ALLOWED_CONTENT_TYPES)[number], number[]> = {
  "image/jpeg": [0xff, 0xd8, 0xff],
  "image/png": [0x89, 0x50, 0x4e, 0x47],
};

export const TenantIdSchema = z.string().regex(/^[a-z0-9][a-z0-9-]{7,63}$/, "invalid tenant id");

/**
 * Display filename only. Object keys never contain it (they use the image id), but filenames are
 * shown in the UI and written to manifests, so strip paths, control chars and exotic characters.
 */
export function sanitizeFilename(raw: string): string {
  const base = raw.split(/[\\/]/).pop() ?? "";
  const cleaned = base
    .normalize("NFKC")
    .replace(/[^A-Za-z0-9._ -]/g, "_")
    .replace(/^\.+/, "_")
    .trim();
  const out = cleaned.slice(0, LIMITS.maxFilenameLength);
  return out.length > 0 ? out : "image";
}
