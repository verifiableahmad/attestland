# The serving image the receipt issuer accepts: upstream vLLM pinned by digest
# at build time (CI resolves the tag to a digest), nothing added at run time.
ARG VLLM_IMAGE=vllm/vllm-openai:v0.11.0
FROM ${VLLM_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/REPLACE-OWNER/attestland" \
      org.opencontainers.image.description="vLLM OpenAI server for attested inference"
ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
