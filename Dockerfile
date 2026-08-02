FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=4318
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p /data
ENV DATABASE_URL=sqlite:////data/worldbuilding.db
EXPOSE 4318
CMD ["gunicorn", "--bind", "0.0.0.0:4318", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
