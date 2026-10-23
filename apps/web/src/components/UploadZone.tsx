"use client";

import { type DragEvent, useId, useRef, useState } from "react";
import { type Api } from "../lib/api.ts";
import { bytes } from "../lib/format.ts";
import { type UploadItem, uploadFiles } from "../lib/upload.ts";

const LABEL: Record<UploadItem["status"], string> = {
  queued: "Queued",
  hashing: "Computing SHA-256",
  uploading: "Uploading",
  verifying: "Verifying on server",
  done: "Verified",
  duplicate: "Duplicate (skipped)",
  rejected: "Rejected",
  error: "Failed",
};
const TONE: Partial<Record<UploadItem["status"], string>> = {
  done: "var(--good)",
  rejected: "var(--bad)",
  error: "var(--bad)",
  duplicate: "var(--warn)",
};

export function UploadZone({
  api,
  datasetId,
  onFinished,
}: {
  api: Api;
  datasetId: string;
  onFinished: () => void;
}) {
  const inputId = useId();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLInputElement>(null);

  const patch = (id: string, p: Partial<UploadItem>) =>
    setItems((cur) => cur.map((i) => (i.id === id ? { ...i, ...p } : i)));

  async function start(files: File[]) {
    if (!files.length || busy) return;
    const next: UploadItem[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      status: "queued",
      progress: 0,
    }));
    setItems(next);
    setBusy(true);
    setError(null);
    try {
      await uploadFiles(api, datasetId, next, patch);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onFinished();
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    void start([...e.dataTransfer.files]);
  };

  return (
    <div className="space-y-3">
      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className="panel flex cursor-pointer flex-col items-center gap-1 border-dashed px-4 py-8 text-center transition-colors focus-within:border-accent hover:border-accent"
        style={over ? { borderColor: "var(--accent)", background: "var(--panel-2)" } : undefined}
      >
        <span className="text-sm font-medium">Drop photos here or choose files</span>
        <span className="text-xs text-muted">
          JPEG or PNG, up to 40 MB each, 80 per dataset. Photos upload directly to object storage.
        </span>
        <input
          id={inputId}
          ref={ref}
          className="sr-only"
          type="file"
          multiple
          accept="image/jpeg,image/png"
          disabled={busy}
          onChange={(e) => {
            void start([...(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
      </label>
      {error ? (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      ) : null}
      {items.length ? (
        <ul
          tabIndex={0}
          aria-label="Upload progress"
          className="panel max-h-72 divide-y divide-line overflow-auto text-sm"
          aria-live="polite"
        >
          {items.map((i) => (
            <li key={i.id} className="flex items-center gap-3 px-3 py-2">
              <span className="min-w-0 flex-1 truncate" title={i.file.name}>
                {i.file.name}
              </span>
              <span className="num text-xs text-muted">{bytes(i.file.size)}</span>
              {i.status === "uploading" ? (
                <progress
                  className="h-1.5 w-24"
                  value={i.progress}
                  max={1}
                  aria-label={`Uploading ${i.file.name}`}
                />
              ) : null}
              <span
                className="w-40 text-right text-xs"
                style={{ color: TONE[i.status] ?? "var(--muted)" }}
                title={i.message}
              >
                {LABEL[i.status]}
                {i.message && (i.status === "rejected" || i.status === "error")
                  ? `: ${i.message}`
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
