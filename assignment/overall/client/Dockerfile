FROM ads:lab

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . /app

CMD ["python", "client.py"]
