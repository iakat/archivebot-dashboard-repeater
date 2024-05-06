FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app
RUN pip install -r requirements.txt
COPY . /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:80", "-b", "0.0.0.0:4568", "--worker-class", "uvicorn.workers.UvicornWorker"]
# gunicorn app:app -b 0.0.0.0:80 -b 0.0.0.0:4568 --worker-class uvicorn.workers.UvicornWorker
