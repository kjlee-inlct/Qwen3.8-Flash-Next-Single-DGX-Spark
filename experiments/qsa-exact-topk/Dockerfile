FROM vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8

LABEL org.opencontainers.image.title="Qwen3.8 exact QSA top-k A/B experiment"
LABEL org.opencontainers.image.description="Pinned base plus only the opt-in exact QSA top-k selector"
LABEL org.opencontainers.image.source="https://github.com/kjlee-inlct/Qwen3.8-Flash-Next-Single-DGX-Spark"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later AND Apache-2.0"

ARG QSA=/usr/local/lib/python3.12/dist-packages/vllm/models/qwen3_8_flash_next/nvidia/ops/qsa.py

COPY patch_qsa_exact_topk.py /tmp/patch_qsa_exact_topk.py
RUN python3 /tmp/patch_qsa_exact_topk.py "${QSA}" \
 && rm /tmp/patch_qsa_exact_topk.py \
 && grep -q 'VLLM_QSA_EXACT_TOPK' "${QSA}" \
 && python3 -m py_compile "${QSA}"
