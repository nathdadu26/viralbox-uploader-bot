FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY uploader.py .

# Environment variables should be set in your deployment platform
# Do not copy .env file - use platform environment variables instead

EXPOSE 8000

CMD ["python", "uploader.py"]
