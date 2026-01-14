FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN apt-get update && apt-get install -y gcc g++ && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get remove -y gcc g++ && apt-get autoremove -y && apt-get clean


COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
