# Measured production profile — 2026-09-07

## Scope

These results describe one qualified DGX Spark deployment of this fork's production profile. They are a same-host before/after throughput comparison and small functional checks, **not a standardized model leaderboard or a universal capacity promise**. They are separate from the upstream author's measurements retained in the README.

The public release generalizes the measured service integration and omits deployment-specific access configuration and private artifacts. It has not thereby become a second hardware qualification. Re-run measurements on your own host, corpus and workload after deployment.

## Platform and model

| Component | Measured configuration |
| --- | --- |
| Hardware | One DGX Spark, NVIDIA GB10, 20 ARM CPU cores |
| Unified memory | Approximately 121.7 GiB |
| CPU governors | Performance on all 20 cores |
| Storage / swap | NVMe; 16 GiB configured swap |
| OS / DGX platform | Ubuntu 24.04.4; DGX OTA 7.5.0 |
| Kernel / driver | `6.17.0-1032-nvidia` / `580.173.02` |
| CUDA / Docker | CUDA 13 / Docker 29.2.1 |
| Host VM settings | `min_free_kbytes=4194304`, `watermark_scale_factor=300`, `swappiness=30` |
| Model | Standard `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4`; not abliterated |
| Model snapshot | `925d7be6c14c6c9442ef83e8f05b5a3c39304f69` |
| Container digest | `sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8` |
| Upstream recipe revision | `78b0675e4469a19befaa9c84a41662b671d4185e` |

The model is a third-party quantization hosted on Hugging Face. The 4 GiB host free-memory reserve changes memory accounting; the upstream author's MemAvailable values cannot be compared directly to these figures. The installed software versions document the environment, not a claim that every available or phased package update was installed.

## Serving configuration

| Parameter | Qualified value |
| --- | --- |
| Maximum context / YaRN | 262,144 tokens / disabled |
| Active slots / batched tokens | 8 / 2,048 |
| KV / recurrent-state dtype | FP8 / BF16 |
| Speculation | MTP depth 3 |
| Model runner / compilation | V2 pinned / mode 0 |
| CUDA graph mode / widths | Full decode only / 4, 8, 12, 16, 20, 24, 28, 32 |
| FlashInfer autotuning | Disabled |
| Fitted draft vocabulary | 55,124 of 248,320 token IDs |
| Host reserve / container limit | 36 GiB / 90 GiB |
| Startup MemAvailable gate | 94 GiB |
| Actual KV pool | 5.41 GiB / 360,264 tokens |

The effective KV pool is shared. It can hold approximately 1.37 maximum-length contexts, **not eight full 262,144-token requests**. The published throughput tests use short prompts. Long prefills and simultaneous long contexts can queue work or affect decoding latency.

The model's default thinking effort is `xhigh`; every fast-mode throughput and qualification result below explicitly disables thinking. No claim about `xhigh` coding performance is inferred from these tests.

## Matched short-request workload: before and after

Each concurrency level submitted the same short developer-oriented explanation prompt, requesting a bounded thread-safe queue with backpressure, cancellation, graceful shutdown, observability and compact Python code. Sampling used temperature 0.2, streaming, a 384-output-token limit, usage reporting and `enable_thinking=false`. Each response reached that limit intentionally.

There was one measured trial at each concurrency after warmup, with warm model/prefix caches. The 1, 2, 4 and 8 levels accounted for 15 requests per sweep. Request start was synchronized with a thread barrier. The benchmark ran against the backend on the serving host, excluding client VPN/network latency.

Definitions:

- **Aggregate tokens/s:** all reported completion tokens divided by wall time for the whole concurrency level, from simultaneous release until the last response finishes. This includes scheduling, prefill and response overhead; it is not pure decode throughput.
- **Per-stream share:** aggregate tokens/s divided by requested concurrency. It is not a median of individually timed streams.
- **TTFT:** elapsed request time to the first nonempty streamed content chunk, not the first HTTP byte or an engine-internal prefill measurement. The table reports the median across requests.
- **Minimum MemAvailable:** the minimum of host samples taken every 250 ms during that workload. Shorter transients can be missed.

| Requests | Before aggregate tok/s | After aggregate tok/s | Before per-stream share tok/s | After per-stream share tok/s | Aggregate change | Before median TTFT | After median TTFT |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 27.57 | 29.72 | 27.57 | 29.72 | +7.8% | 0.516 s | 0.309 s |
| 2 | 40.57 | 50.44 | 20.29 | 25.22 | +24.3% | 0.539 s | 0.389 s |
| 4 | 70.78 | 76.91 | 17.70 | 19.23 | +8.7% | 1.139 s | 1.132 s |
| 8 | 73.66 | **127.61** | 9.21 | 15.95 | **+73.2%** | 10.373 s | **0.651 s** |

| Requests | Before minimum MemAvailable | After minimum MemAvailable | After elapsed wall time | After completion tokens |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 7.57 GiB | 9.32 GiB | 12.920 s | 384 |
| 2 | 7.41 GiB | 9.33 GiB | 15.225 s | 768 |
| 4 | 7.32 GiB | 9.27 GiB | 19.972 s | 1,536 |
| 8 | 7.31 GiB | 9.27 GiB | 24.073 s | 3,072 |

All 15 requests in the tuned sweep completed with nonempty output and the expected token-limit termination. The previous deployment permitted only four active requests, so half the eight-request batch waited; the tuned profile permits eight. This is the main explanation for the eight-request capacity improvement, not evidence of a 73% single-user speedup. The upgrade also changes other runtime settings, reserve choices and source integration, so it is not an isolated A/B for any individual patch.

One trial provides no confidence interval. Content-dependent speculative acceptance, prefix-cache state, thermal state and other work on the host affect the result. In particular, **do not compare these numbers directly to upstream's 600-token prose / three-repeat sparkDash rows or engine-step measurements**. No engine-step or pure-prefill rates were measured by this harness.

### Burst beyond active capacity

A separate 16-request burst used the same 384-token workload:

| Completed | Aggregate | Per-request throughput share | Median TTFT | Wall time | Minimum MemAvailable |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16/16 | 128.49 tok/s | 8.03 tok/s | 11.953 s | 47.816 s | 9.30 GiB |

A metrics snapshot captured **eight running and eight waiting**. This checks queue handling at 16 arrivals, not 16 active inference slots or sustained 16-user latency. A realistic mixed coding workload with long prefills requires additional testing.

## Long-context retrieval

Each test inserted three independently chosen values early, midway and late in a synthetic document (approximately 5%, 50% and 95% of rows). The request asked for all three in strict JSON. Values differed by length and a first-line cache-buster reduced cross-test prefix reuse. The actual chat-template token count was checked against the API usage count.

| Nominal context | Actual prompt tokens | Retrieved values | Strict JSON | Completion tokens | Total request elapsed |
| --- | ---: | ---: | --- | ---: | ---: |
| 32k | 32,740 | 3/3 | Pass | 110 | 19.027 s |
| 128k | 131,072 | 3/3 | Pass | 97 | 72.730 s |
| 250k | 249,990 | 3/3 | Pass | 108 | 148.845 s |

Each request finished normally rather than hitting its 256-token output cap. API counts and tokenization matched. The elapsed values include the entire request and generation; **they are not TTFT or pure prefill time**. This is a three-needle retrieval/formatting test at each length, not evidence of coding, complex reasoning, full-context factual reliability or multi-user performance at 250k.

## Coding qualification and historical comparison

The local suite contained 20 small function-level coding tasks with deterministic checks. Functional scoring tested behavior; strict scoring also enforced the task's instructions. The same saved historical answers were rechecked under the current execution restrictions for the Qwen and Mia DeepSeek comparisons; the older servers were not rerun during this qualification.

| Deployment / answer set | Functional | Strict | Median task latency | Total request latency |
| --- | ---: | ---: | ---: | ---: |
| Qualified Qwen profile | **19/20** | **18/20** | 4.12 s | 106.52 s |
| Historical Qwen | 19/20 | 19/20 | 4.42 s | 118.30 s |
| Historical Mia DeepSeek V4 Flash | 19/20 | 12/20 | 3.38 s | 75.43 s |

Current failures are reported rather than hidden:

- The CSV-record parser failed a unit test; its response was not truncated.
- The shortest-grid-path solution passed functional tests but violated an explicit no-import instruction.

The current score is therefore **one strict pass below historical Qwen**, not an accuracy improvement. Historical timings are from different deployments and dates; they are not a controlled simultaneous speed comparison. This is not SWE-bench and is too small to establish a general model ranking.

Generated code was checked with AST/resource restrictions and a macOS execution sandbox denying network, writes and process forking, with CPU, wall-time and memory bounds. Harness self-tests checked those restrictions. This is a bounded local execution setup, not VM-grade isolation. Private task answers and raw logs are not part of the public release.

## Vision: correctness versus output format

Four synthetic cases checked OCR, object positions/counting, table extraction and reading a small code image. The code image included an explicit bug hint, so that case does not demonstrate independent bug discovery.

| Mode | Objective answers correct | Raw JSON | Schema-valid and correct |
| --- | ---: | ---: | ---: |
| Prompt requests JSON, no constrained format | **4/4** | **0/4** | Not requested |
| Same cases with explicit strict JSON schema | **4/4** | **4/4** | **4/4** |

Every unconstrained answer wrapped its JSON in Markdown fences despite the instruction. The follow-up kept the same four images, prompts, sampling and output limits and added `response_format` with a strict JSON schema. It demonstrates a useful client configuration for these four cases, not a broad vision or structured-output guarantee. Report the constrained result separately; it does not turn the original raw-JSON score into 4/4.

## API and tool compatibility

| Check | Result | What was actually tested |
| --- | --- | --- |
| Authentication | Pass | Unauthenticated request rejected; authenticated model listing succeeded |
| Plain HTTPS streaming | Pass | Trusted-certificate verification, expected content and final SSE terminator |
| Nonstreaming tool arguments | 5/5 exact | One repeated tool schema and argument target |
| Streaming tool round trip | Pass | One streamed tool call, correctly linked mock result, then expected final content |

The streaming tool check made two authenticated requests and verified argument assembly, tool-call ID linkage, finish reasons and stream completion. It executed no real tool or build. API compatibility was exercised through a private-network HTTPS proxy from another computer; no endpoint identifiers, credentials or gateway configuration are published. A specific application's UI integration was not tested or modified.

## Stability and remaining limits

The backend had zero service restarts from its successful startup through the qualification session. Final health checks were successful, with approximately 9.8 GiB MemAvailable and no matching NVIDIA Xid/NVRM or OOM kernel entries since startup. Sustained long-context tests showed GPU temperatures around 66–67 °C. These observations cover the bounded test window only; they are not an overnight soak or a guarantee under every workload.

Keep the production watchdog and memory gates. Load-test your actual mix of prompt lengths, output lengths, thinking efforts and concurrent users. For a stronger performance claim, repeat each level, publish variability and add a mixed long-prefill/short-decode workload. For a stronger quality claim, use a substantially larger, independently specified coding and vision suite.

## Reproducibility and privacy

Public pins, configuration, aggregate measurements and methodology are retained. Deployment-host names, network addresses, account identifiers, raw diagnostics, gateway secrets, the fitted private corpus and its derived vocabulary are omitted. The private vocabulary is a known reproducibility limitation: a fresh local fit can change draft acceptance, so exact published throughput is not promised.

The [sanitized result summary](../bench/results/2026-09-07-profile.json) contains public measurements without deployment endpoint identifiers. The [short-request benchmark](../bench/qualification_load.py) preserves the measured prompt and request parameters, with additional stream/error validation for public use. Its stronger validation does not represent a second measurement of the historical run.

Use the [deployment guide](../deployment/README.md) to prepare the pinned assets and local vocabulary, then qualify the resulting service. From the repository directory **on the Spark**, after the backend is healthy:

```bash
python3 bench/qualification_load.py --levels 1,2,4,8 --max-tokens 64
python3 bench/qualification_load.py --levels 1,2,4,8 --max-tokens 384
python3 bench/qualification_load.py --levels 16 --max-tokens 384
```

The first command warms the short-request paths; the next two reproduce the measured sweep and queued burst shapes. Check each command's output before continuing. The default target is the local backend; the memory sampler reads that machine's host memory. Do not run a second model launcher alongside the managed backend. Benchmark only a service you control, and keep any gateway credentials out of source control.
