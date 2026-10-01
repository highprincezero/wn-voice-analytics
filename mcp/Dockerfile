FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY azurite_audio /app/azurite_audio

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
EXPOSE 8090

CMD ["uvicorn", "azurite_audio.server:app", "--host", "0.0.0.0", "--port", "8090"]
