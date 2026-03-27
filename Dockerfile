# Use official lightweight Python image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Cloud Run uses 8080 by default)
EXPOSE 8080

# Command to run the app using Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "nutrifit60:app"]
