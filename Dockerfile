FROM python:3.10-slim

WORKDIR /app

# Copy dependencies
COPY requirements.txt .

# Install dependencies (dengan --no-cache-dir)
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["python", "main.py"]
