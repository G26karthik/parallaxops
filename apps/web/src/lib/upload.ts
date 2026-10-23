import { ALLOWED_CONTENT_TYPES, LIMITS } from "@parallaxops/contracts";
import { type Api, unwrap } from "./api.ts";
import { sha256Hex } from "./format.ts";

export type UploadStatus =
  | "queued"
  | "hashing"
  | "uploading"
  | "verifying"
  | "done"
  | "duplicate"
  | "rejected"
  | "error";

export interface UploadItem {
  id: string;
  file: File;
  status: UploadStatus;
  progress: number;
  message?: string;
}

type Patch = (id: string, p: Partial<UploadItem>) => void;
const TYPES = new Set<string>(ALLOWED_CONTENT_TYPES);

export function contentTypeOf(f: File): "image/jpeg" | "image/png" | null {
  if (TYPES.has(f.type)) return f.type as "image/jpeg" | "image/png";
  const ext = f.name.toLowerCase().split(".").pop();
  if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
  if (ext === "png") return "image/png";
  return null;
}

function put(
  url: string,
  headers: Record<string, string>,
  file: File,
  onProgress: (f: number) => void,
) {
  return new Promise<void>((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () =>
      x.status >= 200 && x.status < 300
        ? resolve()
        : reject(new Error(`storage returned ${x.status}`));
    x.onerror = () => reject(new Error("network error while uploading"));
    x.send(file);
  });
}

async function pool<T>(items: T[], size: number, fn: (t: T) => Promise<void>) {
  const queue = [...items];
  await Promise.all(
    Array.from({ length: Math.min(size, queue.length) }, async () => {
      for (let t = queue.shift(); t !== undefined; t = queue.shift()) await fn(t);
    }),
  );
}

/** Hash, request presigned URLs, PUT straight to object storage, then ask the API to verify. */
export async function uploadFiles(api: Api, datasetId: string, items: UploadItem[], patch: Patch) {
  const ok: UploadItem[] = [];
  for (const it of items) {
    const ct = contentTypeOf(it.file);
    if (!ct)
      patch(it.id, { status: "rejected", message: "Only JPEG and PNG images are accepted." });
    else if (it.file.size > LIMITS.maxImageBytes)
      patch(it.id, { status: "rejected", message: "Larger than 40 MB." });
    else ok.push(it);
  }
  const hashes = new Map<string, string>();
  for (const it of ok) {
    patch(it.id, { status: "hashing" });
    hashes.set(it.id, await sha256Hex(it.file));
  }
  if (ok.length === 0) return;

  const req = await unwrap(
    api.POST("/v1/datasets/{datasetId}/uploads", {
      params: { path: { datasetId } },
      body: {
        files: ok.map((it) => ({
          filename: it.file.name,
          contentType: contentTypeOf(it.file) as "image/jpeg" | "image/png",
          sizeBytes: it.file.size,
          sha256: hashes.get(it.id) as string,
        })),
      },
    }),
  );
  const toUpload: { it: UploadItem; target: (typeof req.uploads)[number] }[] = [];
  req.uploads.forEach((target, i) => {
    const it = ok[i];
    if (!it) return;
    if (target.action === "DUPLICATE")
      patch(it.id, {
        status: "duplicate",
        progress: 1,
        message: "Identical image already in this dataset.",
      });
    else toUpload.push({ it, target });
  });

  const uploaded: string[] = [];
  await pool(toUpload, 3, async ({ it, target }) => {
    patch(it.id, { status: "uploading", progress: 0 });
    try {
      await put(target.url as string, target.headers, it.file, (f) =>
        patch(it.id, { progress: f }),
      );
      patch(it.id, { status: "verifying", progress: 1 });
      uploaded.push(target.imageId);
    } catch (e) {
      patch(it.id, { status: "error", message: (e as Error).message });
    }
  });
  if (uploaded.length === 0) return;
  const done = await unwrap(
    api.POST("/v1/datasets/{datasetId}/uploads/complete", {
      params: { path: { datasetId } },
      body: { imageIds: uploaded },
    }),
  );
  const byId = new Map(done.images.map((i) => [i.id, i]));
  for (const { it, target } of toUpload) {
    const img = byId.get(target.imageId);
    if (!img) continue;
    if (img.status === "UPLOADED") patch(it.id, { status: "done" });
    else if (img.status === "REJECTED")
      patch(it.id, {
        status: "rejected",
        message: img.rejectReason ?? "Rejected by verification.",
      });
  }
}
