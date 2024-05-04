FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app
RUN pip install -r requirements.txt
COPY . /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4568"]
