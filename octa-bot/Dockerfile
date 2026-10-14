FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 sentinel
COPY sentinel ./sentinel
RUN mkdir /app/data && chown -R sentinel:sentinel /app
USER sentinel
ENV HOST=0.0.0.0 PORT=8080 DATABASE_PATH=/app/data/sentinel.db
EXPOSE 8080
CMD ["python", "-m", "sentinel"]
