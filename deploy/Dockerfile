# SMS agent receiver + inline worker.
#
# Deliberately NOT the main SiftStack image: no Playwright, no OpenCV, no
# Tesseract. The cloud box uses the smrtPhone API transport (verified live), so
# the browser session fallback is a local-only path and the image stays small
# and fast to redeploy.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY deploy/requirements-sms-agent.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Only the agent package. Everything it needs is env-driven, and the reisift
# client falls back to the standalone Api-Key path when there is no Deal Room
# checkout on disk, which there never is here.
COPY src/sms_agent ./sms_agent

# sms_agent/daily25 (the Wendy daily-25 picker) reuses siftline_kpi.Api for
# its own JWT mint + SiftLine board pull rather than a third copy of that
# ~20-line auth block, so the single stdlib-only file rides along too.
COPY src/siftline_kpi.py ./siftline_kpi.py

# Caller -> Slack id map, read UNDER the /data copy (config.BUNDLED_CONFIG
# resolves to /config here), so a mapping fixed in git reaches production on a
# plain deploy without anyone having to edit the volume. sms_senders.json is
# gitignored on purpose and stays volume-only.
COPY config/slack_ids.json /config/

# The SQLite event log lives on the mounted volume so it survives a redeploy.
ENV SMS_AGENT_DATA_DIR=/data \
    SMS_AGENT_DB=/data/sms_agent.db \
    SMS_AGENT_INLINE_WORKER=1 \
    SMS_AGENT_TRANSPORT=api \
    SMS_AGENT_HOST=0.0.0.0 \
    SMS_AGENT_PORT=8080 \
    PYTHONPATH=/app

EXPOSE 8080

# The container has no .env; every secret arrives as a Fly secret.
CMD ["uvicorn", "sms_agent.receiver:app", "--host", "0.0.0.0", "--port", "8080"]
