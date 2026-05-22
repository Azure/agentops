# AgentOps E2E hello-agent
#
# Tiny FastAPI service exposing POST / -> {"text": "..."} backed by a
# Microsoft Agent Framework agent that calls Azure OpenAI (gpt-4o-mini).
#
# Built and pushed to the per-environment ACR by the e2e workflow's
# bootstrap-live job, then deployed as a per-run Azure Container App so
# the http-aca scenario exercises a real LLM (not just an echo).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
