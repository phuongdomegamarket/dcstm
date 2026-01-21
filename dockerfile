# Base Python slim (nhẹ, Debian-based, dễ install Node)
FROM python:3.12-slim

# Cài dependencies hệ thống + FFmpeg mới nhất
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    ffmpeg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js LTS mới nhất (từ nodesource, evergreen ~22.x)
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest  # update npm nếu cần

# Kiểm tra versions (debug)
RUN node --version && npm --version && python --version && ffmpeg -version

# Set working dir
WORKDIR /app

# Copy và install Python deps trước (cache tốt)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy và install Node deps
COPY package*.json ./
RUN npm ci  # hoặc npm install

# Copy toàn bộ code
COPY . .

# Expose port cho HF Spaces
EXPOSE 7860

# CMD chạy app chính (thay bằng của bạn)
# Ví dụ: Python FastAPI/Gradio-like
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
# Hoặc Node: CMD ["npm", "start"]
# Hoặc hybrid: một script shell chạy cả 2 nếu cần (nhưng tránh)
