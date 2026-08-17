FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# Most PaaS hosts inject $PORT. Default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py", "--http"]
