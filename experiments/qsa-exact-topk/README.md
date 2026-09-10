# Exact QSA top-k correctness A/B image

This experiment starts from the exact production image digest and changes only
the Qwen3.8 QSA block-selection call. When
`VLLM_QSA_EXACT_TOPK=1`, the stock persistent top-k selector is replaced by
`torch.topk` over visible columns.

The purpose is causal isolation: the strengthened GB10 baseline and the
Mamba-only experiment were both deterministic at zero context and unstable at
8K/32K. The Mamba-only fixes did not change that boundary.

## Scope

Included:

1. The opt-in exact QSA top-k patch.
2. A planned-outage runner using the strengthened cache/QSA validator.
3. Automatic restoration and health verification of the pinned production
   service on every exit.

Excluded:

- Mamba state-copy and block-size patches;
- deterministic standalone CUDA extensions;
- FLA, PLE, FP8, AutoRound, MTP and GEMM changes;
- any production service or model-profile change.

This exact implementation is expected to reduce prefill performance. It is a
correctness probe, not a production performance recommendation.

## Provenance

`patch_qsa_exact_topk.py` is preserved byte-for-byte from
`blazux/qwen3.8-Flash-DGX` commit
`bd60fcb1b492ca920f74df7462f05da7b6d98f73`:

- path: `src/patch_qsa_exact_topk.py`
- SHA-256: `006b1133b3be7e3e59927ce9811c6a9fa285332ea56a1c67d7edb49012748c06`

The source and vLLM are Apache-2.0. Preserve their notices.

## Build

```bash
bash experiments/qsa-exact-topk/build.sh
```

The build uses the locally present, digest-pinned production base and performs
no network download.

## Run

Run only during a planned outage:

```bash
sudo bash experiments/qsa-exact-topk/run-ab-root.sh
```

The runner refuses to start without a healthy production baseline, enables
`VLLM_QSA_EXACT_TOPK=1` only inside the experimental container, writes a
sanitized report and container log under `/var/tmp`, and restores the pinned
production service.

Pass criteria:

- cache 8K/32K/131K: valid samples, prefix hit, equal text/token fingerprints,
  and equal first-token logprobs;
- QSA 0/8K/32K: every repeat stable by the same measures;
- no runtime exception;
- verified restoration to the pinned production image.

Do not merge the runtime patch into production from this experiment. If it
establishes causality, evaluate the separately pinned deterministic CUDA kernel
as a second performance-qualified implementation.
