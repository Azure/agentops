FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install AgentOps with the agent extras (FastAPI + Azure Monitor + crypto).
ARG AGENTOPS_VERSION=
RUN if [ -n "$AGENTOPS_VERSION" ]; then \
      pip install "agentops-toolkit[agent]==${AGENTOPS_VERSION}"; \
    else \
      pip install "agentops-toolkit[agent] @ git+https://github.com/Azure/agentops.git@develop"; \
    fi

# Optional: copy a workspace into the image so analyze can find historic
# runs. In production you would mount this as a volume instead.
COPY .agentops /app/.agentops

EXPOSE 8080

CMD ["agentops", "agent", "serve", "--host", "0.0.0.0", "--port", "8080"]
