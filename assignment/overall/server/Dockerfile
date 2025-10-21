FROM ads:lab

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 18861

CMD ["python", "server.py"]
