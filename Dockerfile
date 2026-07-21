FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

ENV STATE_FILE=/app/data/seen.json
VOLUME ["/app/data"]

CMD ["python", "-u", "bot.py"]
