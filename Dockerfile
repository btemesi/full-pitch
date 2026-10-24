FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY fullpitch ./fullpitch
COPY web ./web
COPY data/FP-0001 ./data/FP-0001
EXPOSE 8000
# one process: viewer sessions and the live clock live in memory
CMD ["uvicorn", "fullpitch.server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
