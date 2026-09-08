# Production integration and tuning notes

This fork is based on public MiaAI upstream revision `78b0675e4469a19befaa9c84a41662b671d4185e`. The measured serving profile was qualified on one DGX Spark on 2026-09-07. See the [measured report](docs/measured-profile-2026-09-07.md) for results and the [deployment guide](deployment/README.md) for the generalized public installer.

## Attribution: what is upstream and what is added here

MiaAI's recipe supplies the checkpoint layout, PLE offload and packed-table path, inference patch generators, reduced-vocabulary MTP support, FP8 KV support, recurrent-state tuning, decode-graph configuration and underlying memory-watchdog mechanism. vLLM and its dependencies provide the serving runtime. This fork does not claim those as new kernel work.

This fork adds the pinned production profile, service lifecycle and recovery integration, stricter offline-startup controls, host-specific reserve choices, deployment safeguards and the measurements below. Several features already existed upstream as configurable options; the contribution is selecting, integrating and qualifying them together on this host.

## Inputs and provenance

| Input | Pin used for qualification |
| --- | --- |
| Model repository | `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4` |
| Hugging Face snapshot | `925d7be6c14c6c9442ef83e8f05b5a3c39304f69` |
| Container | `vllm/vllm-openai:qwen38-flash-next` |
| Container digest | `sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8` |
| Upstream recipe | `78b0675e4469a19befaa9c84a41662b671d4185e` |

The model is a **third-party quantization on Hugging Face**, not a checkpoint distributed by Qwen itself. Existing cached weights were reused for the measured upgrade. The optional gated abliterated checkpoint was not downloaded, deployed or benchmarked. It is a different model choice, not a prerequisite for this profile.

Normal managed startup resolves the pinned local snapshot and existing image. It uses offline Hugging Face/Transformers settings and prevents an implicit image pull. Fetching assets is a deliberate provisioning step, not an action hidden inside routine service recovery. Check licenses and review future source changes before updating pins.

## Serving profile and tradeoffs

| Setting | Measured value | Reason / limitation |
| --- | --- | --- |
| Context / YaRN | 262,144 / off | Native context; no extrapolated 512k claim |
| Active request slots | 8 | Raises short-request capacity; not eight full-context requests |
| Prefill chunk size | 2,048 tokens | Retained measured compromise; long prefills can still delay live streams |
| MTP depth | 3 | Uses upstream speculative decoding and target verification |
| KV dtype | FP8 | Reduces KV footprint |
| Recurrent SSM state | BF16 | Uses upstream optimized recurrent-state profile |
| Decode graph captures | 4, 8, 12, 16, 20, 24, 28, 32 | Covers each MTP-3 verify width for 1–8 active sequences |
| Compilation mode | 0 | Retains the measured non-compile profile |
| Model runner | V2 pinned | Avoids an implicit runner change between starts |
| FlashInfer autotune | Disabled | Retains the qualified startup/runtime choice |
| Fitted draft vocabulary | 55,124 / 248,320 token IDs | Workload-specific; generated locally and not published |

Reduced-vocabulary drafting is an **upstream technique**: the draft projection reads fewer rows and the target model still verifies proposals. The measured vocabulary used only locally available material and locally generated examples; its builder ran without network access. Publishing that corpus or its derived vocabulary was not necessary to publish the production integration. Rebuild a vocabulary appropriate for your workload and remeasure; a different corpus changes acceptance and performance.

The runtime reported a 0.26 GiB draft `lm_head` instead of 1.18 GiB, with approximately 2.76 GiB fewer bytes read per MTP-3 step. The latter is a bandwidth estimate per step, **not** an additional 2.76 GiB resident-memory saving. This deployment did not run an isolated draft-vocabulary A/B.

The deployed chat template enables thinking by default and chooses `xhigh` when no effort is supplied. The published throughput and small qualification tests explicitly disable thinking. Their latency is therefore not an estimate of `xhigh` latency. Supported template effort values are `low`, `medium` and `xhigh`; clients should not assume every provider's effort labels map identically.

## Reserve-aware memory budget

The preceding working profile used a 34 GiB host-reserve setting. During system maintenance, host memory pressure crossed its watchdog threshold and stopped inference. The revised profile retains that safety mechanism, raises the reserve setting to 36 GiB, keeps an explicit 90 GiB container limit and consistently requires 94 GiB MemAvailable before launch.

The measured host also reserves 4 GiB through `vm.min_free_kbytes`, with `vm.watermark_scale_factor=300` and `vm.swappiness=30`. These kernel settings affect MemAvailable accounting, so raw memory numbers must not be compared directly with a differently tuned host. This is a platform-specific profile, not a claim that every Spark should use identical reserves.

`KV_TARGET_GIB=20` expresses a wish, not a guaranteed allocation. After other runtime allocations and budget caps, the actual KV allocation was **5.41 GiB / 360,264 tokens**. That is about 1.37 times one maximum-length request, shared across the scheduler. Eight request slots are suitable for the tested short prompts; they do not create eight 262k context windows.

The watchdog retains its 4 GiB MemAvailable floor and its 4 GiB MemFree floor when availability is below 10 GiB. The container's cgroup limit does not cap every GPU-driver allocation. Keep the watchdog and monitor host memory; do not increase capacity simply because a theoretical KV calculation fits.

The eight-stream workload's sampled minimum MemAvailable increased from 7.31 to 9.27 GiB. The production change improves headroom in this test; it does not establish a universal safe load limit.

## Startup, cutover and recovery

The managed service uses `Restart=on-failure`, a 60-second restart delay and a limit of two starts within one hour: **an initial start plus one automatic retry**, then operator intervention. The runner waits for memory recovery before launch, and propagates unexpected container exits as failures instead of leaving a nominally successful service with no model serving.

The deployment path stages a release, validates it, checks current request activity when available, preserves the preceding release and verifies readiness before declaring success. It also handles the backend already being offline—the prior installer assumed its metrics endpoint was always reachable. Failed cutovers retain a rollback path. See [deployment/README.md](deployment/README.md) for exact current behavior and prerequisites.

Maintenance is separate from inference benchmarking. Large package operations can evict caches and put pressure on the same unified memory used by the model; plan a maintenance window and stop serving before such work. A startup timeout is not evidence that the model is healthy: verify `/health` and a real request. The measured cold start took approximately 12 minutes, including weight loading, MTP and vision warmup.

## Exposure, privacy and audit boundaries

The inference backend binds to loopback. Authentication, trusted TLS, access control and any private-network proxy must be supplied separately; the public repository does not publish a working credential or a deployment-specific gateway. Loopback binding alone does not make an unauthenticated remote proxy safe.

Standard telemetry and implicit Hugging Face token discovery are disabled, and draft-vocabulary tokenizer remote code is disabled. These controls reduce accidental outbound behavior, but **offline model loading is not a network-egress firewall**, and this work is not a proof that every binary or transitive dependency is free of telemetry. For strict egress isolation, design and test a host/container firewall policy separately.

The public source omits deployment-host identifiers, private gateway configuration, credentials, raw hardware diagnostics, the fitted corpus and private benchmark answers. Public aggregate results are reported with methodology and limitations. The generalized public release is not byte-identical to the private deployment integration that was measured; new installs should run their own readiness and qualification checks.

## What the measurements do—and do not—establish

The same short-request test improved from 73.66 to 127.61 aggregate tokens/s at eight requests (+73.2%), largely by allowing eight active requests instead of four. One-request throughput rose from 27.57 to 29.72 tokens/s (+7.8%). Several settings changed together; these are before/after deployment results, not an isolated causal measurement of any one patch.

Coding remained 19/20 functional but fell from historical Qwen's 19/20 to 18/20 on strict instruction compliance. Vision was correct on four examples but needed explicit JSON-schema mode for unfenced JSON. Long-context tests checked retrieval, not large-project coding. Zero backend restarts occurred during the bounded qualification window; an overnight soak and realistic mixed production load remain separate work.

Use the [full measured profile](docs/measured-profile-2026-09-07.md) before comparing these numbers to upstream, other models or another host.
