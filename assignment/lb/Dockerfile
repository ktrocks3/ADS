FROM python:3.10-slim

WORKDIR /app
COPY load_balancer_FD.py .

# no external libs needed, but if you do:
# COPY requirements.txt .
# RUN pip install -r requirements.txt

EXPOSE 9000
CMD ["python", "load_balancer_FD.py"]
