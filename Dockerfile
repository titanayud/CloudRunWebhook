FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment & port config
ENV PORT=8080
EXPOSE 8080

# Jalankan Flask via Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]
