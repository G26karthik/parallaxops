import { createHash } from "node:crypto";
import {
  CreateBucketCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  HeadBucketCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  PutBucketCorsCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import type { Config } from "../config.ts";

export interface ObjectHead {
  bytes: number;
}

/** Storage adapter. Local dev uses SeaweedFS; any S3-compatible service works unchanged. */
export interface ObjectStore {
  /**
   * The URL only accepts exactly `bytes` bytes of `contentType` whose SHA-256 is `sha256Hex`; the
   * client must send the `x-amz-checksum-sha256` header (base64, see `checksumHeader`).
   */
  presignPut(
    key: string,
    contentType: string,
    bytes: number,
    sha256Hex: string,
    ttlSeconds: number,
  ): Promise<string>;
  presignGet(key: string, ttlSeconds: number, downloadName?: string): Promise<string>;
  head(key: string): Promise<ObjectHead | null>;
  /** First `length` bytes, used for magic-byte sniffing without downloading the object. */
  readPrefix(key: string, length: number): Promise<Uint8Array>;
  /** Streams the whole object once and returns its SHA-256. */
  sha256(key: string): Promise<string>;
  /** SHA-256 (hex) the store verified while receiving the object, or null if it kept none. */
  storedSha256(key: string): Promise<string | null>;
  put(key: string, body: Uint8Array | string, contentType: string): Promise<void>;
  get(key: string): Promise<Uint8Array>;
  delete(key: string): Promise<void>;
  list(prefix: string): Promise<{ key: string; bytes: number; lastModified: Date }[]>;
  ping(): Promise<void>;
}

/** Value of the `x-amz-checksum-sha256` header for a hex SHA-256. */
export const checksumHeader = (sha256Hex: string) =>
  Buffer.from(sha256Hex, "hex").toString("base64");

export class S3Store implements ObjectStore {
  private readonly client: S3Client;
  /** Separate client whose endpoint is what the browser can reach. */
  private readonly presigner: S3Client;
  private readonly cfg: Config;

  constructor(cfg: Config) {
    this.cfg = cfg;
    const base = {
      region: cfg.S3_REGION,
      forcePathStyle: true,
      credentials: { accessKeyId: cfg.S3_ACCESS_KEY, secretAccessKey: cfg.S3_SECRET_KEY },
      requestChecksumCalculation: "WHEN_REQUIRED" as const,
      responseChecksumValidation: "WHEN_REQUIRED" as const,
    };
    this.client = new S3Client({ ...base, endpoint: cfg.S3_ENDPOINT });
    this.presigner = new S3Client({ ...base, endpoint: cfg.s3PublicEndpoint });
  }

  private get bucket() {
    return this.cfg.S3_BUCKET;
  }

  /** Creates the bucket and CORS policy if missing. Safe to call on every boot. */
  async ensureBucket(): Promise<void> {
    try {
      await this.client.send(new HeadBucketCommand({ Bucket: this.bucket }));
    } catch {
      await this.client.send(new CreateBucketCommand({ Bucket: this.bucket }));
    }
    try {
      await this.client.send(
        new PutBucketCorsCommand({
          Bucket: this.bucket,
          CORSConfiguration: {
            CORSRules: [
              {
                AllowedOrigins: this.cfg.corsOrigins,
                AllowedMethods: ["GET", "PUT", "HEAD"],
                AllowedHeaders: ["*"],
                ExposeHeaders: ["ETag"],
                MaxAgeSeconds: 600,
              },
            ],
          },
        }),
      );
    } catch {
      // Some servers configure CORS at process level (SeaweedFS -s3.allowedOrigins); not fatal.
    }
  }

  presignPut(
    key: string,
    contentType: string,
    bytes: number,
    sha256Hex: string,
    ttlSeconds: number,
  ) {
    // content-length is signed: the client sends the real body length, so a larger or smaller
    // upload fails the signature check and never reaches the bucket. The digest is signed as a
    // header (not hoisted into the query string) and the store checks it against the bytes it
    // receives, answering 400 BadDigest on a mismatch.
    return getSignedUrl(
      this.presigner,
      new PutObjectCommand({
        Bucket: this.bucket,
        Key: key,
        ContentType: contentType,
        ContentLength: bytes,
        ChecksumSHA256: checksumHeader(sha256Hex),
      }),
      {
        expiresIn: ttlSeconds,
        signableHeaders: new Set(["content-type", "content-length", "x-amz-checksum-sha256"]),
        unhoistableHeaders: new Set(["x-amz-checksum-sha256"]),
      },
    );
  }

  presignGet(key: string, ttlSeconds: number, downloadName?: string) {
    return getSignedUrl(
      this.presigner,
      new GetObjectCommand({
        Bucket: this.bucket,
        Key: key,
        ...(downloadName
          ? {
              ResponseContentDisposition: `attachment; filename="${downloadName.replace(/"/g, "")}"`,
            }
          : {}),
      }),
      { expiresIn: ttlSeconds },
    );
  }

  async head(key: string): Promise<ObjectHead | null> {
    try {
      const r = await this.client.send(new HeadObjectCommand({ Bucket: this.bucket, Key: key }));
      return { bytes: r.ContentLength ?? 0 };
    } catch (e) {
      if (
        (e as { name?: string }).name === "NotFound" ||
        (e as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode === 404
      )
        return null;
      throw e;
    }
  }

  async readPrefix(key: string, length: number): Promise<Uint8Array> {
    const r = await this.client.send(
      new GetObjectCommand({ Bucket: this.bucket, Key: key, Range: `bytes=0-${length - 1}` }),
    );
    return (await r.Body?.transformToByteArray()) ?? new Uint8Array();
  }

  async sha256(key: string): Promise<string> {
    const r = await this.client.send(new GetObjectCommand({ Bucket: this.bucket, Key: key }));
    const h = createHash("sha256");
    for await (const chunk of r.Body as AsyncIterable<Uint8Array>) h.update(chunk);
    return h.digest("hex");
  }

  async storedSha256(key: string): Promise<string | null> {
    const r = await this.client.send(
      new HeadObjectCommand({ Bucket: this.bucket, Key: key, ChecksumMode: "ENABLED" }),
    );
    // A multipart object reports a checksum of checksums ("...-N"); that is not the file's digest.
    if (!r.ChecksumSHA256 || r.ChecksumSHA256.includes("-")) return null;
    return Buffer.from(r.ChecksumSHA256, "base64").toString("hex");
  }

  async put(key: string, body: Uint8Array | string, contentType: string) {
    await this.client.send(
      new PutObjectCommand({ Bucket: this.bucket, Key: key, Body: body, ContentType: contentType }),
    );
  }

  async get(key: string): Promise<Uint8Array> {
    const r = await this.client.send(new GetObjectCommand({ Bucket: this.bucket, Key: key }));
    return (await r.Body?.transformToByteArray()) ?? new Uint8Array();
  }

  async delete(key: string) {
    await this.client.send(new DeleteObjectCommand({ Bucket: this.bucket, Key: key }));
  }

  async list(prefix: string) {
    const out: { key: string; bytes: number; lastModified: Date }[] = [];
    let token: string | undefined;
    do {
      const r = await this.client.send(
        new ListObjectsV2Command({ Bucket: this.bucket, Prefix: prefix, ContinuationToken: token }),
      );
      for (const o of r.Contents ?? [])
        out.push({
          key: o.Key ?? "",
          bytes: o.Size ?? 0,
          lastModified: o.LastModified ?? new Date(0),
        });
      token = r.IsTruncated ? r.NextContinuationToken : undefined;
    } while (token);
    return out;
  }

  async ping() {
    await this.client.send(new HeadBucketCommand({ Bucket: this.bucket }));
  }
}

/** In-memory store for tests. Presigned URLs are opaque tokens; tests PUT bytes via `putBytes`. */
export class MemoryStore implements ObjectStore {
  readonly objects = new Map<string, { body: Uint8Array; contentType: string; at: Date }>();
  /** Digests a real store would have verified at upload; tests set these to exercise that path. */
  readonly storedChecksums = new Map<string, string>();

  async presignPut(key: string, _ct: string, _bytes: number, _sha: string, ttl: number) {
    return `memory://put/${encodeURIComponent(key)}?ttl=${ttl}`;
  }
  async presignGet(key: string, ttl: number) {
    return `memory://get/${encodeURIComponent(key)}?ttl=${ttl}`;
  }
  /** Test helper standing in for the browser's direct upload. */
  putBytes(key: string, body: Uint8Array, contentType = "image/jpeg") {
    this.objects.set(key, { body, contentType, at: new Date() });
  }
  async head(key: string) {
    const o = this.objects.get(key);
    return o ? { bytes: o.body.length } : null;
  }
  async readPrefix(key: string, length: number) {
    return this.objects.get(key)?.body.slice(0, length) ?? new Uint8Array();
  }
  async sha256(key: string) {
    return createHash("sha256")
      .update(this.objects.get(key)?.body ?? new Uint8Array())
      .digest("hex");
  }
  async storedSha256(key: string) {
    return this.storedChecksums.get(key) ?? null;
  }
  async put(key: string, body: Uint8Array | string, contentType: string) {
    this.objects.set(key, {
      body: typeof body === "string" ? new TextEncoder().encode(body) : body,
      contentType,
      at: new Date(),
    });
  }
  async get(key: string) {
    const o = this.objects.get(key);
    if (!o) throw new Error(`no such key ${key}`);
    return o.body;
  }
  async delete(key: string) {
    this.objects.delete(key);
  }
  async list(prefix: string) {
    return [...this.objects.entries()]
      .filter(([k]) => k.startsWith(prefix))
      .map(([key, o]) => ({ key, bytes: o.body.length, lastModified: o.at }));
  }
  async ping() {}
}
