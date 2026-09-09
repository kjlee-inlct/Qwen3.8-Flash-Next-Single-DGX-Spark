FROM vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8

LABEL org.opencontainers.image.title="Qwen3.8 Mamba/prefix A-B experiment"
LABEL org.opencontainers.image.description="Pinned base plus only Mamba prefix alignment and state-copy correctness fixes"
LABEL org.opencontainers.image.source="https://github.com/kjlee-inlct/Qwen3.8-Flash-Next-Single-DGX-Spark"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later AND Apache-2.0"

ARG VLLM_ROOT=/usr/local/lib/python3.12/dist-packages/vllm

COPY patch_mamba_align_split.py /tmp/patch_mamba_align_split.py
RUN python3 /tmp/patch_mamba_align_split.py \
 && rm /tmp/patch_mamba_align_split.py

# Exact-file replacement is deliberately tied to the exact base-image digest.
# It carries merged vLLM PR #50729 plus the bounded invalid-block guard.
COPY mamba_utils_guarded.py ${VLLM_ROOT}/v1/worker/mamba_utils.py
RUN python3 -m py_compile \
      ${VLLM_ROOT}/v1/core/sched/scheduler.py \
      ${VLLM_ROOT}/v1/worker/gpu/model_states/mamba_hybrid.py \
      ${VLLM_ROOT}/v1/worker/mamba_utils.py
