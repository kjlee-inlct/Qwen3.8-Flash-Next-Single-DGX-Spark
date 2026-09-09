# Mamba/prefix correctness A/B image

This experiment starts from the exact production base-image digest and applies
only the Mamba-related correctness changes needed to isolate the failure found
by `bench/cache_correctness.py`.

Included:

1. Scheduler chunk splitting uses `mamba_block_size`.
2. Align-mode state-slot seeding uses `mamba_block_size`.
3. The Mamba state-copy implementation carries merged vLLM PR #50729's
   overlap-safe copy and a bounded invalid-block guard.

Excluded: deterministic/exact QSA top-k, AutoRound weights, hybrid FP8 dispatch,
never-evict pinning, PLE implementation changes, and performance patches.

## Provenance

The two source payloads are copied without local semantic edits from
`Saren-Arterius/qwen3.8-Flash-DGX-AutoRound` commit
`1633d4bc11701c04d7cbd633994421466d1eff1d`:

- `src/patch_mamba_align_split.py`, blob `c331c82500e3cb45e58463eaf7b8623d86f695ca`
- `src/mamba_utils_guarded.py`, blob `713e1ba1b1818844b364377f256dc92b17825294`

That repository is Apache-2.0 and the replacement file is derived from vLLM,
also Apache-2.0. This repository remains AGPL-3.0-or-later; preserve all notices.

## Build only

```bash
docker build --pull=false \
  -t qwen38-flash-next:mamba-prefix-ab \
  experiments/mamba-prefix
```

The Dockerfile performs syntax checks against the pinned image. Building does
not stop or alter the production service.

## Runtime A/B boundary

Do not point the production systemd runner at this mutable local tag: it
correctly refuses any image other than its pinned production digest. Test during
a planned outage with the explicit harness that will be added after the image
build succeeds and its local digest is recorded.

Use the unchanged baseline report in
`bench/results/2026-09-09-cache-qsa-validation.json`. The patched run must use
the same model revision, context sizes, repeat count and sampling parameters.
Pass criteria:

- all cache cases: prefix hit increases, answer hashes match, first-token
  logprobs match;
- QSA zero/8K/32K cases: answer hashes and first-token logprobs remain stable;
- no Mamba guard violations or state-copy errors in container logs.

If cache correctness passes but long-context QSA still fails, stop this
experiment and create a second image containing deterministic top-k. Do not add
that patch here.
