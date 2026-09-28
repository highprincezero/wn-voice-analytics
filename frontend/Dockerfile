FROM python:3.12-slim

WORKDIR /app

COPY frontend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY frontend/streamlit_app.py frontend/api_client.py /app/
COPY frontend/.streamlit /app/.streamlit
COPY samples /app/samples

EXPOSE 8501

CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
