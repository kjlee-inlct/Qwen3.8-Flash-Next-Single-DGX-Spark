# Community fork integration review

This branch reviews changes made after `kjlee-inlct/main` revision
`78b0675e4469a19befaa9c84a41662b671d4185e`. Changes are selected by operational
safety and portability rather than merged wholesale.

## Adopted

### anubhavmalhotra: `d25e9a8`

Adopted the reserve-aware production profile and its deployment layer:

- pinned container digest and Hugging Face snapshot;
- offline runtime and explicit image provisioning;
- loopback-only backend binding;
- immutable manifest-listed releases with readiness checks and rollback;
- bounded systemd recovery and host-memory admission checks;
- local draft-vocabulary validation, qualification harness, tests and measured notes.

The profile was measured on one DGX Spark and remains opt-in. Its 36 GiB host
reserve, kernel VM settings, eight short-request slots and workload-fitted draft
vocabulary are not universal defaults for every Spark. The generalized installer completed a clone-to-runtime validation on one GB10
DGX Spark on 2026-09-09; this remains one-host evidence, not universal portability. The release ID is changed to `78b0675-community-v1` so it cannot be
mistaken for or collide with the source fork's immutable release.

### catonooka: `9d00bf6`

Adopted the `download.sh` error-message construction fix. The prior f-string used
opposite quote styles in a conditional expression and caused Python to fail while
parsing every fresh download invocation.

## Deferred

### catonooka performance campaign: `be3225b` through `4365b68`

Deferred the vLLM `#50729` and `#53388` backports, skinny-GEMM image patch and
associated tuned defaults. They mount thousands of lines copied from particular
vLLM internals over the container installation and therefore have a narrow image
compatibility window. Their warm-turn and throughput results are useful evidence,
but adoption should use a dedicated pinned image plus isolated correctness,
long-context and rollback qualification. The benchmark scripts and reports can be
ported separately without enabling the runtime patches.

### bmhaskar: `f25495f` through `bb17930`

Deferred `.env` and `.last_launch.sh`. They capture a valuable known-running host,
but include `/home/bharat/...` paths, a specific container memory limit and an
older generated launch that intentionally bypasses current `start.sh`. `RUNNING.md`
is retained as external operational evidence rather than copied into the generic
recipe. Its key lesson—decode throughput varies strongly with prompt type and MTP
acceptance—should be considered when comparing benchmark numbers.

## Validation boundary

The integration is checked with Bash syntax validation, Python unit tests for the
deployment helpers and load harness, manifest verification and Git whitespace
checks. A real GB10 DGX Spark run on 2026-09-09 completed the pinned model/image bootstrap,
privileged systemd installation, smoke test and mixed-load test with exit status
0. Prefix hits increased 0 to 4,992 and cold/warm TTFT measured 5.590/1.886
seconds. A subsequent 20-repeat greedy test at 32K kept text and first-token top-logprobs
stable; prefix hits increased 0 to 29,952 and TTFT measured 18.809 seconds cold
versus 1.848 seconds warm. An overnight soak, OS/driver matrix and future
image/model revisions remain separate qualification tasks.

## Follow-up review: independent DGX Spark recipes

The independent `blazux/qwen3.8-Flash-DGX` and
`dolf3131/qwen3.8-flash-next-dgx-spark` repositories do not share this Git
history and target different checkpoint/runtime combinations. Their image-level
Mamba, QSA top-k, PLE mmap, mixed-precision and TP=1 skinny-GEMM patches are not
copied into the production image without reproduction on this pinned stack.

The immediately portable parts are adopted as `bench/runtime_validation.py`:

- repeated greedy text and first-token logprob stability checks;
- long real-text cold/warm TTFT with an explicit prefix-cache metric check;
- baseline versus competing-long-prefill decode stream-gap measurement;
- loopback-only operation that stores no generated answer text.

The launcher also rejects `--async-scheduling` when MTP is enabled. The reviewed
recipe reports that this combination can let speculative placeholder indices
reach Qwen3.8-Flash-Next's n-gram context path and silently alter output. This is
a correctness guard, not a performance tuning claim. Image-level changes remain
deferred until the new validation tool establishes behavior on the target DGX.

## Current follow-up decision

### Do now: non-mutating evidence

- The 20-repeat deterministic smoke and required-prefix-hit check completed on
  2026-09-09. Repeat it after any image, model, kernel or cache-policy change.
- `bench/storage_prefill.py` now provides non-mutating real-text 8K/32K/128K
  TTFT, prefix-hit, major-fault, memory, swap, process-I/O, physical-device and
  packed-PLE-size observation. Hardware results are pending.
- Record KV capacity on natural service starts so launch-to-launch variation can
  be assessed without creating an outage solely for measurement.

### Defer until evidence justifies a maintenance experiment

- Sweep `long-prefill-token-threshold` only if production traffic shows harmful
  decode gaps. It trades prefill throughput for stream responsiveness and needs
  controlled restarts plus an A/B report.
- Evaluate deterministic top-k, Mamba cache fixes, skinny-GEMM and MTP index
  sharing only in a separately built, digest-pinned experimental image after a
  failure or material bottleneck is reproduced on the current image.

### Do not apply to the current Mia checkpoint profile

- NVIDIA-official-checkpoint compatibility patches and the roughly 128 GiB swap
  design solve a different model/layout problem and conflict with this profile's
  memory policy. They belong in a separate profile with separate qualification.
- Host-specific `.env`, generated launch files, unauthenticated non-loopback
  binding and unbounded container restart policies remain excluded.
