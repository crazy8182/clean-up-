FROM python:3.12.2

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip --root-user-action=ignore &&     pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

COPY . .

CMD ["python3", "bot.py"]
