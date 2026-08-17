#!/bin/bash

# Kiểm tra xem Ollama server đã chạy chưa, nếu chưa thì tự động khởi động
if ! curl -s http://localhost:11435/api/tags > /dev/null; then
  echo "Ollama server is not running. Starting it now..."
  OLLAMA_HOST=127.0.0.1:11435 ollama serve > ollama.log 2>&1 &
  # Đợi một chút để server lên hẳn
  sleep 5
else
  echo "Ollama server is already running."
fi

echo "Starting continuous worker..."
.venv/bin/python main.py worker
