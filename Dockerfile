FROM python:3.11-slim

# Install system dependencies for OpenCV and FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsm6 libxext6 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED 1

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
