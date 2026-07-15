FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system lynxpay && useradd --system --gid lynxpay --home-dir /app lynxpay

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

RUN chown -R lynxpay:lynxpay /app
USER lynxpay

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
