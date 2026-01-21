# Sử dụng Node.js 22 (latest stable LTS) trên Alpine nhẹ
FROM node:22-alpine

# Cài ffmpeg mới nhất + các lib cần thiết
RUN apk update && apk add --no-cache \
    ffmpeg \
    && rm -rf /var/cache/apk/*

# (Nếu cần ffmpeg full features hơn, dùng base Debian và compile hoặc apt)
# FROM node:22-slim
# RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Tạo user non-root (HF khuyến nghị UID 1000)
RUN addgroup -g 1000 appgroup && adduser -u 1000 -G appgroup -D appuser
USER appuser
WORKDIR /home/appuser/app

# Copy package files trước để cache layer npm install
COPY --chown=appuser:appgroup package*.json ./
RUN npm ci --omit=dev   # Hoặc npm install nếu cần dev deps

# Copy toàn bộ code
COPY --chown=appuser:appgroup . .

# Expose port (HF Spaces dùng 7860 mặc định)
EXPOSE 7860

# Command chạy app (thay bằng của bạn: node server.js, npm start, node index.js,...)
CMD ["npm", "start"]
# Hoặc CMD ["node", "server.js"]
