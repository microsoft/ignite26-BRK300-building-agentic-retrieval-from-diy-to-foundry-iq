FROM python:3.12-slim AS build

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN groupadd --system app && useradd --system --gid app app
WORKDIR /app
COPY --from=build /install /usr/local
# The build context is the app folder; copy it to /app/app so imports stay app.*.
COPY --chown=app:app . ./app
USER app
EXPOSE 8000

CMD ["gunicorn", "app.main:app", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "180"]