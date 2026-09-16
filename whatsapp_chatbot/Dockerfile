# Use Python 3.10 base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Set environment variables for unbuffered output
ENV PYTHONUNBUFFERED=1

# Install system dependencies (ffmpeg for audio processing)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY whatsapp_chatbot/requirements.txt requirements.txt
RUN pip install -r requirements.txt

# Copy service code and shared module
COPY whatsapp_chatbot/ .

# Accept PORT as build argument with default value
ARG PORT=5001
ENV PORT=${PORT}

# Expose the port dynamically
EXPOSE ${PORT}

# Run the application with dynamic port
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}