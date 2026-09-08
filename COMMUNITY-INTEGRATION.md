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
vocabulary are not universal defaults for every Spark. The generalized installer
has helper tests but has not been proven by a clean-machine installation in this
repository. The release ID is changed to `78b0675-community-v1` so it cannot be
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
checks. A real DGX Spark launch, model download, privileged systemd installation,
GPU load test and overnight soak remain hardware validation tasks.
