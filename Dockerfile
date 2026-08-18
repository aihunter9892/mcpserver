FROM python:3.12-slim

# Never buffer logs; they must reach the platform's log collector immediately.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy requirements first so dependency layers cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py guards.py ./

# Run as a non-root user. If the process is ever compromised, it has no
# privileges to escalate with.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENV PORT=8000
EXPOSE 8000

# /health is a plain JSON endpoint. Never health-check /mcp: it is an SSE
# stream that stays open, so the probe hangs and the platform kills the service.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health',timeout=4)"

CMD ["python", "server.py", "--http"]
