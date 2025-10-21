FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true
COPY load_balancer_FD.py .

CMD ["python", "load_balancer_FD.py"]
