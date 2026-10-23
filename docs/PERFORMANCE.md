# Measured performance

Everything here was measured on the development laptop (AMD Ryzen 9 8945HS, 16 threads, 31.2 GiB
RAM with roughly 9 GiB free during these runs, RTX 4060 Laptop unused, Windows 11) and is stored in
`benchmarks/results/`. Nothing is extrapolated. Scale beyond one machine is **designed for, not
tested**.

## Reconstruction (CPU only, single job)

ETH3D terrace, 23 DSLR images, 1600 px working size, through the full platform
(`benchmarks/results/gate2-e2e/completed_job.json`):

| Stage | Time |
|-------|-----:|
| Download images (23, SHA-256 verified) | 3.3 s |
| Image QC | 5.7 s |
| Prepare (orient, resize) | 15.0 s |
| SIFT feature extraction | 9.8 s |
| Matching + geometric verification (253 pairs) | 41.0 s |
| Sparse mapping + bundle adjustment | 7.9 s |
| Metrics, export, publish | 0.7 s |
| **Total** | **87 s** (the CLI alone, without download and publish steps: 67 to 73 s over four runs) |

Peak process-tree RSS 3.8 GiB. Submit-to-202 latency: **46 ms**.

## Dense reconstruction (GPU, opt-in)

COLMAP 4.2.1 CUDA build, same terrace scene (23 images), dense working size 1000 px, geometric
consistency on. Two measured runs:

| Run | Dense stage | Points | GPU memory in use (peak) | Evidence |
|-----|------------:|-------:|-------------------------:|----------|
| Through the platform (job with `dense: true`) | 344 s (PatchMatch 340 s, fusion 3 s) | 249,005 (249,005 in the browser preview, decimation cap is 300k) | 678 MiB | `benchmarks/results/gate5/dense_job.json` |
| Standalone probe before integration | 773 s | 253,904 | 1,545 MiB | measured by hand, not committed |

The two runs differ by 2.2x in time and in memory with the same inputs and settings. The cause was
not investigated; a plausible factor is laptop GPU power and thermal state, and the "memory in use"
figure includes other processes on the GPU (desktop, browser). Treat the dense numbers as a range.
Peak GPU memory stayed far below the 8 GiB budget; the one-heavy-job-at-a-time rule means dense never
runs alongside another GPU job.

## Control-plane API under bounded load

Closed-loop clients on the same machine over loopback, zero think time, 15 s per phase after a 2 s
warm-up; mix 5 list-projects : 5 get-job : 2 list-jobs : 1 create-project : 1 healthz. The get-job
reads exist only in the "during reconstruction" phases (there is no job to read while idle).
**This measures HTTP behaviour of one Node process against real Postgres, not reconstruction
throughput; exactly one reconstruction ran at a time.** Source: `benchmarks/api_load.py`,
`benchmarks/results/gate4/api_load.json`.

| Condition | Virtual users | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|-----------|--------------:|-------------------:|---------:|---------:|---------:|-------:|
| idle | 1 | 294 | 3.3 | 4.9 | 5.7 | 0 |
| idle | 8 | 362 | 14.1 | 67.8 | 138 | 0 |
| idle | 32 | 253 | 91.4 | 351 | 536 | 0 |
| reconstruction running | 1 | 218 | 4.5 | 6.8 | 8.1 | 0 |
| reconstruction running | 8 | 231 | 21.4 | 114 | 212 | 0 |
| reconstruction running | 32 | 214 | 99.9 | 433 | 652 | 0 |

Reading it: throughput saturates at roughly 250 to 360 req/s here because the load generator,
API, Postgres and (in the second half) a CPU-saturating reconstruction all share 16 threads; with a
closed loop, latency is then queueing (32 users / 253 req/s is about 126 ms mean, consistent with
Little's law). A running reconstruction cost about 15 to 36% of API throughput and raised p95
latency by about 70% at 8 users (68 to 114 ms); it caused no errors. Run the API on separate cores or hosts for headroom.

## Queueing and cancellation (real processes)

| Measurement | Result | Evidence |
|-------------|--------|----------|
| Second job while the first runs | waited 89.6 s in `QUEUED`; never more than 1 job active | `gate2-e2e/queueing.json` |
| Cancel to `CANCELLED` | 6.1 s (was 33.5 s before the heartbeat timeout was shortened from 45 s to 10 s; the SDK throttles heartbeats to 80% of the timeout, which bounds cancel delivery) | `gate2-e2e/cancel.json` |
| Worker hard-killed mid-job | attempt 1 recorded `FAILED` (worker lost), retried on a new worker, job `COMPLETED`; total recovery 74.7 s | `gate2-e2e/worker_kill_retry.json` |
| API restarted mid-job | job unaffected, `COMPLETED` | `gate2-e2e/api_restart.json` |
| Orphan engine after worker kill | none (Job Object kill-on-close) | `test_resilience.py` |
| Scratch left after kill / cancel / failure | none (owner-pid sweeper) | `test_no_scratch_files_or_engines_remain` |
