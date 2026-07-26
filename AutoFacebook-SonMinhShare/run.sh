#!/bin/bash
# Universal Facebook Group Auto Poster - Chạy nhanh

echo "🎯 Universal Facebook Group Auto Poster"
echo "======================================"
echo "Script đơn giản, ổn định, chạy được trên mọi máy"
echo ""

# Nạp .env nếu có
# --- NẠP ENV AN TOÀN (thay cho export $(grep ...)) ---
if [ -f ".env" ]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi
# -----------------------------------------------------

# Kiểm tra Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 chưa được cài đặt!"
    echo "💡 Cài đặt: sudo apt install python3 python3-pip"
    exit 1
fi

# Kiểm tra file cần thiết (giữ nguyên logic hiện tại)
if [ "$MODE" == "JOIN_BY_LIST" ]; then
    if [ ! -f "$TASK4JOIN_CSV" ]; then
        echo "❌ Không tìm thấy file: $TASK4JOIN_CSV"
        exit 1
    fi
else
    if [ ! -f "$DEFAULT_GROUPS_CSV" ]; then
        echo "❌ Không tìm thấy file: $DEFAULT_GROUPS_CSV"
        exit 1
    fi
    if [ ! -f "$DEFAULT_CONTENT_TXT" ]; then
        echo "❌ Không tìm thấy file: $DEFAULT_CONTENT_TXT"
        exit 1
    fi
fi

echo "✅ Tất cả file cần thiết đã có!"

# Kiểm tra virtual environment
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment chưa được tạo!"
    echo "💡 Chạy: python3 -m venv venv"
    exit 1
fi

# Kích hoạt virtual environment
echo "🔧 Kích hoạt virtual environment..."
# shellcheck disable=SC1091
source venv/bin/activate

echo "🚀 Bắt đầu chuẩn bị chạy Python..."
echo ""

# ======= MỚI: Chọn file .py để chạy =======
# Thu thập danh sách .py trong thư mục hiện tại (không đệ quy)
mapfile -t PY_FILES < <(find . -maxdepth 1 -type f -name "*.py" -printf "%f\n" | LC_ALL=C sort)

if [ ${#PY_FILES[@]} -eq 0 ]; then
  echo "❌ Không tìm thấy file .py nào trong thư mục hiện tại."
  deactivate 2>/dev/null
  exit 1
fi

DEFAULT_INDEX=""
# Nếu có POSTER.py thì đặt làm mặc định
for i in "${!PY_FILES[@]}"; do
  if [ "${PY_FILES[$i]}" = "POSTER.py" ]; then
    DEFAULT_INDEX=$((i+1))
    break
  fi
done

echo "📜 Danh sách file Python (.py) trong thư mục hiện tại:"
for i in "${!PY_FILES[@]}"; do
  idx=$((i+1))
  mark=""
  if [ -n "$DEFAULT_INDEX" ] && [ "$idx" -eq "$DEFAULT_INDEX" ]; then
    mark=" (mặc định)"
  fi
  echo "  $idx) ${PY_FILES[$i]}$mark"
done

echo ""
if [ -n "$DEFAULT_INDEX" ]; then
  read -r -p "👉 Nhập số để chạy (Enter để chạy mặc định: ${PY_FILES[$((DEFAULT_INDEX-1))]}): " CHOICE
else
  read -r -p "👉 Nhập số để chạy: " CHOICE
fi

# Xử lý lựa chọn
if [ -z "$CHOICE" ]; then
  if [ -n "$DEFAULT_INDEX" ]; then
    CHOICE=$DEFAULT_INDEX
  else
    echo "❌ Bạn phải nhập một số hợp lệ."
    deactivate 2>/dev/null
    exit 1
  fi
fi

# Kiểm tra CHOICE là số và trong phạm vi
if ! [[ "$CHOICE" =~ ^[0-9]+$ ]]; then
  echo "❌ Lựa chọn không hợp lệ: $CHOICE"
  deactivate 2>/dev/null
  exit 1
fi

if [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -gt "${#PY_FILES[@]}" ]; then
  echo "❌ Số lựa chọn vượt phạm vi."
  deactivate 2>/dev/null
  exit 1
fi

TARGET_FILE="${PY_FILES[$((CHOICE-1))]}"

echo ""
echo "🚀 Chạy: python3 \"$TARGET_FILE\""
echo "======================================"
python3 "$TARGET_FILE"
EXIT_CODE=$?

# Thoát venv khi xong
deactivate 2>/dev/null

# Propagate exit code
exit $EXIT_CODE
