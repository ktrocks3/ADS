FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true
COPY load_balancer_FD.py load_balancer_gui.py run_lb.sh ./

CMD ["./run_lb.sh"]
