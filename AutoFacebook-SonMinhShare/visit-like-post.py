#!/home/mike/Desktop/CDHA.ai/Automation/BTV-TUAN-ANH/auto/venv/bin/python3
# -*- coding: utf-8 -*-
"""
Universal Facebook Group Auto Poster — Mã Hoàn Chỉnh (đã bổ sung JOIN_BY_LIST)

Các tính năng (cũ):
- Selenium Chrome (webdriver_manager)
- Giữ xuống dòng chuẩn (Lexical): chèn JS + ENTER thật
- Random interactions (scroll/mở tab About/Members/Photos, mở post, random Like)
- Chế độ chạy: POST_ONLY | INTERACT_ONLY | POST_PLUS_INTERACT
- DEDUP API chống đăng trùng
- Bỏ qua đăng nếu bài cũ đang chờ duyệt
- Tự động bình luận bài chào mừng + Like 3–5 bài

TÍNH NĂNG MỚI:
##- MODE: JOIN_BY_LIST — Tham gia Group theo danh sách task4join.csv
- Tự trả lời câu hỏi và đồng ý điều khoản khi xin vào nhóm (dùng OpenAI API)
- Ghi trạng thái vào cột thứ 4 (Status) của task4join.csv
- Nhóm đã là thành viên → chỉ tương tác tự nhiên, không đăng bài
- Toàn bộ cấu hình có thể đặt qua .env (có mặc định)
"""

import os
import csv
import time
import random
import hashlib
import platform
import requests
from dataclasses import dataclass

# --- Thêm các import này ---
import atexit
import fcntl
import socket
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, NoSuchElementException
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from webdriver_manager.chrome import ChromeDriverManager

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ======= Cấu hình qua ENV (có mặc định) =======
def env(key, default=None):
    v = os.getenv(key)
    return v if (v is not None and str(v).strip() != "") else default

def env_bool(key: str, default=False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")

# >>> ADD (gần các ENV ở đầu file)
VISIT_LIKE_CSV = env("VISIT_LIKE_CSV", "data/task4join.csv")  # CSV có header No,Name,URL,Status,Public,Member,Post,LastUpdated


OPENAI_API_KEY    = env("OPENAI_API_KEY", "")
OPENAI_MODEL      = env("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_DOMAIN    = env("DEFAULT_DOMAIN", "y tế")  # lĩnh vực để AI bám vào khi trả lời câu hỏi xin vào nhóm

# ======= DEDUP API (server SQLite autopost.php) =======
DEDUP_API_BASE = env("DEDUP_API_BASE", "https://upload.cdha.ai/api/autopost.php")
DEDUP_API_KEY  = env("DEDUP_API_KEY", "chuoi-bi-mat-dai-ngu-nhien")

# ======= Tuỳ chọn trình bày =======
ENABLE_BEAUTIFY = True
BULLET_CONTACT  = True

# ======= Danh sách nhóm đăng bài (chế độ cũ) =======
RANDOMIZE_ORDER     = True
RANDOM_SAMPLE_COUNT = None    # None = tất cả; ví dụ 10 → lấy ngẫu nhiên 10 nhóm

# ======= Mouse người thật =======
ENABLE_HUMAN_MOUSE = True
MOUSE_BASE_SPEED   = 900  # px/s

# ======= Tương tác =======
ENABLE_BETWEEN_INTERACTIONS = True
INTERACTION_COUNT_RANGE     = (2, 5)
LIKE_PROBABILITY            = 0.35
VISIT_LIKE_COMMENTS        = env_bool("VISIT_LIKE_COMMENTS", default=False)  # Like bình luận? Mặc định: không

# ======= VISIT_LIKE Post Settings =======
VISIT_LIKE_ENABLE_POST     = env_bool("VISIT_LIKE_ENABLE_POST", default=True)  # Có đăng bài trong VISIT_LIKE mode?
VISIT_LIKE_POST_PROBABILITY = float(env("VISIT_LIKE_POST_PROBABILITY", "1.0"))  # Xác suất đăng bài (0.0-1.0)

# ======= Delay giữa mỗi nhóm =======
DEFAULT_DELAY_RANGE = (3, 10)

# ======= CHẾ ĐỘ CHẠY =======
# "POST_ONLY" | "INTERACT_ONLY" | "POST_PLUS_INTERACT" | "JOIN_BY_LIST" | "VISIT_LIKE"
MODE = env("MODE", "JOIN_BY_LIST").upper()

# ======= File I/O mặc định =======
DEFAULT_GROUPS_CSV  = env("DEFAULT_GROUPS_CSV", "data/selected_recruitment_groups.csv")
DEFAULT_CONTENT_TXT = env("DEFAULT_CONTENT_TXT", "data/post_content.txt")
DEFAULT_IMAGE_PATH  = env("DEFAULT_IMAGE_PATH", "data/post1.png")

# ======= Posts Directory =======
POSTS_DIR = env("POSTS_DIR", "posts")
POSTS_CONTENTS_FILE = env("POSTS_CONTENTS_FILE", "posts/contents.txt")

# ======= File JOIN =======
TASK4JOIN_CSV = env("TASK4JOIN_CSV", "data/task4join.csv")  # cột 1: name, cột 2: url, cột 3: notes (tùy), cột 4: Status

# ======= Danh sách bình luận mẫu cho CDHA.ai =======
CDHA_COMMENTS = [
    "🌟 Hello Diagnostic Imaging community! We're CDHA.ai (𝐂𝐃 𝐇𝐚), an advanced AI tool empowering radiologists with fast, accurate analysis across X-Ray, CT, MRI, Ultrasound, PET, and Nuclear Medicine. With 1,868 diseases in 14 categories and 208 subgroups, we save time and reduce errors. Who's using AI in their workflow? DM us to explore how we can help! 😊",
    "📸 Greetings, radiologists! Struggling with heavy imaging workloads? CDHA.ai (𝐂𝐃 𝐇𝐚) is here to streamline your process! We analyze X-Ray, CT, MRI, PET, and more, covering 1,868 diseases with precision. Want to cut reporting time and boost accuracy? Drop us a message to learn more! 💡",
    "🩺 Hi everyone! We're CDHA.ai (𝐂𝐃 𝐇𝐚), your AI partner for diagnostic imaging. From X-Ray to Nuclear Medicine, we handle 1,868 conditions across 14 categories, helping you save time and minimize errors. Has anyone tried AI for complex cases? Let's chat—DM us for a demo! 🙌",
    "🔍 Hey Diagnostic Imaging pros! Meet CDHA.ai (𝐂𝐃 𝐇𝐚), the AI that supercharges radiology workflows. We process X-Ray, CT, MRI, Ultrasound, PET, and more, detecting 1,868 diseases in 208 subgroups. Ready to optimize your practice? Comment or DM us to see how we work! 🚀",
    "💻 Hello radiology community! CDHA.ai (𝐂𝐃 𝐇𝐚) is designed to tackle massive imaging volumes across X-Ray, CT, MRI, and PET, with insights on 1,868 diseases. We help radiologists save time and enhance accuracy. Facing challenges with high caseloads? Message us to discover our solutions! 😄",
    "🌐 Hi Diagnostic Imaging group! We're CDHA.ai (𝐂𝐃 𝐇𝐚), an AI tool that supports radiologists with X-Ray, CT, MRI, Ultrasound, PET, and Nuclear imaging. Our system covers 1,868 diseases in 14 categories, reducing errors and speeding up reports. Who's interested in a trial? DM us! 💬",
    "🩻 Greetings, rads! CDHA.ai (𝐂𝐃 𝐇𝐚) is your AI co-pilot for diagnostic imaging, analyzing everything from X-Ray to PET scans and identifying 1,868 diseases across 208 subgroups. We're here to ease your workload. Got a tricky case? Let us help—send a DM! 😊",
    "📷 Hello imaging experts! CDHA.ai (𝐂𝐃 𝐇𝐚) is an advanced AI that processes X-Ray, CT, MRI, Ultrasound, PET, and more, covering 1,868 conditions. We help radiologists work faster and smarter. Curious about AI in radiology? Drop a comment or DM us for details! 🚀",
    "🩺 Hi Diagnostic Imaging folks! We're CDHA.ai (𝐂𝐃 𝐇𝐚), built to support radiologists with multi-modal analysis (X-Ray, CT, MRI, PET, etc.) and insights on 1,868 diseases. Save time, reduce errors, and focus on complex cases. Who's tried AI tools? Let's connect—DM us! 💡",
    "🔬 Hello radiology community! CDHA.ai (𝐂𝐃 𝐇𝐚) is your go-to AI for analyzing X-Ray, CT, MRI, Ultrasound, PET, and Nuclear Medicine scans, with 1,868 diseases categorized for precision. Want to streamline your workflow? Message us to learn how we can assist! 😄"
]


# ---------------- DEDUP API helpers ----------------
def dedup_request(mode, group, actor="", content="",
                  window_seconds=86400, lease_seconds=600, timeout=15):
    try:
        payload = {
            "mode": mode,
            "group": group,
            "actor": actor,
            "window_seconds": window_seconds,
            "lease_seconds": lease_seconds
        }
        if content:
            payload["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        headers = {"X-Api-Key": DEDUP_API_KEY}
        r = requests.post(DEDUP_API_BASE, data=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"ok": False, "error": f"http_error: {e}"}

# ---------------- OpenAI helper (trả lời câu hỏi xin vào nhóm) ----------------
def ai_answer(short_question: str, group_title_or_topic: str = "", domain: str = DEFAULT_DOMAIN, lang_hint: str = "auto") -> str:
    """
    Trả về một đoạn text ngắn, lịch sự, phù hợp câu hỏi gia nhập group (đa ngôn ngữ).
    Dùng OpenAI Responses API. Nếu thiếu API key → trả lời fallback cơ bản.
    """
    base_fallback = "I am interested in the group topic and agree to abide by the rules. Thank you."
    if not OPENAI_API_KEY:
        return base_fallback

    prompt = f"""You are generating concise, friendly answers to Facebook group join questions.
Group context: "{group_title_or_topic}". Domain: {domain}. Language hint: {lang_hint}.
Question: "{short_question}"
Constraints:
- 1–2 sentences only, polite, specific to the group topic.
- If rules agreement is implied, acknowledge you'll follow them.
- Avoid sharing personal sensitive data. No links unless explicitly asked.
Return only the answer text."""

    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": OPENAI_MODEL,
            "input": [
                {"role": "system", "content": "You are a helpful assistant that writes succinct, polite answers."},
                {"role": "user", "content": prompt}
            ]
        }
        r = requests.post("https://api.openai.com/v1/responses", json=payload, headers=headers, timeout=25)
        r.raise_for_status()
        data = r.json()
        # Hỗ trợ cả schema mới/ cũ: cố lấy 'output_text' trước, fallback sang field khác
        text = None
        try:
            text = data.get("output_text")
        except Exception:
            pass
        if not text:
            # fallback rất thận trọng
            text = base_fallback
        text = (text or "").strip()
        if not text:
            text = base_fallback
        # Cắt gọn
        return text[:500]
    except Exception:
        return base_fallback

@dataclass
class JoinResult:
    status: str
    detail: str = ""

# ===== Persistent Chrome Profile helpers =====
def _env_bool(name: str, default=False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y")

def _find_free_tcp_port(start=9222, end=9550):
    for p in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0  # không tìm thấy (rất hiếm)

def _ensure_profile_dir() -> Path:
    """
    Profile cố định:
    - Ưu tiên ENV FB_POSTER_PROFILE (có thể đặt trong .env)
    - Mặc định: ~/.cache/auto-join-ai/chrome-profile
    """
    custom = os.environ.get("FB_POSTER_PROFILE", "").strip()
    if custom:
        base = Path(os.path.expanduser(custom)).resolve()
    else:
        base = Path.home() / ".cache" / "auto-join-ai" / "chrome-profile"
    base.mkdir(parents=True, exist_ok=True)
    return base

def _try_lock_profile(profile_dir: Path):
    """
    Tạo file lock để tránh 2 tiến trình ghi cùng 1 profile.
    Trả về (lock_fd, profile_dir) nếu giữ lock thành công, ngược lại (None, None).
    """
    lock_file = profile_dir / ".profile.lock"
    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(lock_fd, str(os.getpid()).encode("utf-8"))
        os.fsync(lock_fd)
        return lock_fd, profile_dir
    except BlockingIOError:
        os.close(lock_fd)
        return None, None

def _prepare_persistent_profile():
    """
    Cố gắng dùng profile chính; nếu đang bị khoá, xét FB_POSTER_PROFILE_SECONDARY:
    - Nếu FB_POSTER_PROFILE_SECONDARY=1 (mặc định): tạo profile phụ `chrome-profile-<pid>`.
    - Nếu =0: ném lỗi yêu cầu đóng phiên khác trước.
    """
    main_dir = _ensure_profile_dir()
    lock_fd, locked_dir = _try_lock_profile(main_dir)
    if lock_fd is not None:
        return locked_dir, lock_fd, True  # dùng profile chính

    if not _env_bool("FB_POSTER_PROFILE_SECONDARY", default=True):
        raise RuntimeError(
            f"Profile đang được sử dụng: {main_dir}. "
            f"Đóng phiên khác hoặc đặt FB_POSTER_PROFILE_SECONDARY=1 để dùng profile phụ."
        )

    # Tạo profile phụ theo PID
    alt_dir = main_dir.parent / f"{main_dir.name}-{os.getpid()}"
    alt_dir.mkdir(parents=True, exist_ok=True)
    lock_fd2, locked_dir2 = _try_lock_profile(alt_dir)
    if lock_fd2 is None:
        # Hiếm khi trùng; thử hậu tố ngẫu nhiên
        import random
        rnd = random.randint(1000, 9999)
        alt_dir = main_dir.parent / f"{main_dir.name}-{os.getpid()}-{rnd}"
        alt_dir.mkdir(parents=True, exist_ok=True)
        lock_fd2, locked_dir2 = _try_lock_profile(alt_dir)
        if lock_fd2 is None:
            raise RuntimeError("Không thể tạo/khoá profile phụ cho phiên này.")

    return locked_dir2, lock_fd2, False  # dùng profile phụ



class UniversalFacebookPoster:
    def __init__(self):
        self.driver = None
        self.wait = None
        self.setup_driver()
        atexit.register(self.close)

    def _hover_and_react(self, like_btn, prefer=("love", "like")):
        """
        Di chuyển chuột đến nút Like để mở khay reaction, rồi chọn 'Yêu thích' (love)
        hoặc 'Thích' (like). Trả về True nếu đã thực hiện hành động, False nếu bỏ qua
        (ví dụ đã like sẵn aria-pressed='true').
        """
        try:
            ap = (like_btn.get_attribute('aria-pressed') or '').lower()
            if ap == 'true':
                return False
            # hover
            try:
                x, y = self._element_center_in_viewport(like_btn)
                self._human_move_to_xy(int(x), int(y))
            except Exception:
                pass
            time.sleep(random.uniform(0.25, 0.55))
            # Tìm icon reaction ưu tiên
            pref_map = {
                'love': [
                    "//*[@aria-label='Yêu thích']",
                    "//*[@aria-label='Love']",
                ],
                'like': [
                    "//*[@aria-label='Thích']",
                    "//*[@aria-label='Like']",
                ],
            }
            # vùng tìm kiếm toàn trang nhưng xuất hiện ngay sau hover
            for choice in prefer:
                for xp in pref_map.get(choice, []):
                    els = self.driver.find_elements(By.XPATH, xp)
                    if els:
                        el = els[0]
                        try:
                            self._human_click(el)
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", el)
                        time.sleep(random.uniform(0.25, 0.5))
                        return True
            # nếu không mở được khay reaction → bấm Like thường
            try:
                self._human_click(like_btn)
            except Exception:
                self.driver.execute_script("arguments[0].click();", like_btn)
            time.sleep(random.uniform(0.25, 0.5))
            return True
        except Exception:
            return False

    def _norm_status(self, s: str) -> str:
        """Chuẩn hoá giá trị Status về: request_sent | already_member | joined | (khác giữ nguyên)"""
        s = (s or "").strip().lower()
        s = s.replace("-", "_").replace(" ", "_")

        # Việt/Anh hay gặp
        if "đã_tham_gia" in s or "thanh_vien" in s:
            return "already_member"
        if "đã_gửi_yêu_cầu" in s or "cho_duyet" in s or "chờ_duyệt" in s or "request" in s or "requested" in s:
            return "request_sent"

        # Map phổ biến
        mapping = {
            "requested": "request_sent",
            "request": "request_sent",
            "already_member": "already_member",
            "member": "already_member",
            "joined": "joined",
        }
        return mapping.get(s, s)

    # >>> ADD vào trong class UniversalFacebookPoster

    def _read_groups_for_visit_like(self, csv_path: str):
        """
        Đọc CSV header: No,Name,URL,Status,Public,Member,Post,LastUpdated
        Lấy các dòng có Status ∈ {request_sent, already_member, joined}.
        Trả về: list[dict] với row_index để cập nhật LastUpdated.
        """
        rows_out = []
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if not rows:
                return rows_out

            header = [c.strip() for c in rows[0]]
            col = {name.lower(): i for i, name in enumerate(header)}
            # Bắt buộc các cột chính
            need = ["url", "status"]
            if not all(n in col for n in need):
                print("❌ CSV thiếu cột URL/Status bắt buộc."); return rows_out

            start = 1
            for i in range(start, len(rows)):
                r = rows[i]
                if not r or len(r) <= col["url"]:
                    continue
                url = (r[col["url"]] or "").strip()
                status_raw = (r[col["status"]] or "").strip()
                if not url:
                    continue
                norm = self._norm_status(status_raw)
                if norm in ("request_sent", "already_member", "joined"):
                    name = (r[col.get("name", -1)] if "name" in col and len(r) > col["name"] else url).strip()
                    rows_out.append({
                        "row_index": i,
                        "name": name or url,
                        "url": url,
                        "status": norm
                    })
            print(f"✅ VISIT_LIKE: tìm thấy {len(rows_out)} nhóm hợp lệ trong {csv_path}")
        except Exception as e:
            print(f"❌ Lỗi đọc CSV VISIT_LIKE: {e}")
        return rows_out

    def _write_last_updated(self, csv_path: str, row_index: int, dt_str: str):
        """Ghi LastUpdated cho dòng row_index, giữ nguyên các cột khác."""
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            if row_index < 0 or row_index >= len(rows):
                print(f"⚠️  row_index {row_index} không hợp lệ khi ghi LastUpdated."); return
            header = [c.strip() for c in rows[0]]
            col = {name.lower(): i for i, name in enumerate(header)}
            if "lastupdated" not in col:
                # nếu không có cột, thêm vào cuối header và các dòng
                header.append("LastUpdated")
                for k in range(1, len(rows)):
                    rows[k].append("")
                rows[0] = header
                col["lastupdated"] = len(header) - 1
            j = col["lastupdated"]
            row = rows[row_index]
            while len(row) <= j:
                row.append("")
            row[j] = dt_str
            rows[row_index] = row
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerows(rows)
            print(f"📝 Đã cập nhật LastUpdated={dt_str} cho dòng {row_index}")
        except Exception as e:
            print(f"❌ Lỗi ghi LastUpdated: {e}")


    # >>> ADD vào trong class UniversalFacebookPoster

    # Bỏ hàm comment_on_random_posts để code gọn hơn

    def simple_post_to_group(self, content, image_path=None):
        """
        Hàm đăng bài đơn giản - chỉ tập trung vào việc đăng bài
        """
        try:
            print("📝 Bắt đầu đăng bài đơn giản...")
            
            # Cuộn lên đầu trang
            self.driver.execute_script("window.scrollTo({top:0,behavior:'instant'});")
            time.sleep(random.uniform(1.0, 2.0))
            
            # Tìm và click vào ô "Bạn viết gì đi..."
            composer_selectors = [
                "//span[contains(text(),'Bạn viết gì')]/ancestor::div[@role='button']",
                "//span[contains(text(),'What\\'s on your mind')]/ancestor::div[@role='button']",
                "//div[@data-pagelet^='GroupInlineComposer']//div[@role='button']",
                "//div[contains(@aria-label,'Bạn viết gì')]",
                "//div[contains(@aria-label,'What\\'s on your mind')]",
            ]
            
            composer_clicked = False
            for selector in composer_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    if elements:
                        element = elements[0]
                        if element.is_displayed() and element.is_enabled():
                            print(f"🔍 Tìm thấy composer với selector: {selector}")
                            self._human_click(element)
                            composer_clicked = True
                            break
                except Exception as e:
                    print(f"⚠️ Lỗi với selector {selector}: {e}")
                    continue
            
            if not composer_clicked:
                print("❌ Không tìm thấy composer để đăng bài")
                return False
            
            # Đợi dialog mở
            time.sleep(random.uniform(2.0, 3.0))
            
            # Tìm ô nhập text
            textbox_selectors = [
                "//div[@role='dialog']//div[@role='textbox' and @contenteditable='true']",
                "//div[@role='dialog']//div[@contenteditable='true']",
                "//div[@role='textbox' and @contenteditable='true']",
            ]
            
            textbox = None
            for selector in textbox_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    if elements:
                        textbox = elements[0]
                        print(f"🔍 Tìm thấy textbox với selector: {selector}")
                        break
                except Exception as e:
                    print(f"⚠️ Lỗi với textbox selector {selector}: {e}")
                    continue
            
            if not textbox:
                print("❌ Không tìm thấy textbox để nhập nội dung")
                return False
            
            # Nhập nội dung
            print("📝 Đang nhập nội dung...")
            self._human_click(textbox)
            time.sleep(random.uniform(0.5, 1.0))
            
            # Clear và nhập text mới
            self._type_multiline_js_plus_enter(textbox, content)
            time.sleep(random.uniform(1.0, 2.0))
            
            # Upload hình ảnh nếu có
            if image_path and os.path.exists(image_path):
                print(f"🖼️ Đang upload hình ảnh: {image_path}")
                try:
                    # Tìm nút upload ảnh
                    photo_selectors = [
                        "//div[@role='dialog']//div[@aria-label='Thêm ảnh/video']",
                        "//div[@role='dialog']//div[@aria-label='Add Photo/Video']",
                        "//div[@role='dialog']//*[contains(text(),'Thêm ảnh')]",
                        "//div[@role='dialog']//*[contains(text(),'Add photo')]",
                    ]
                    
                    photo_clicked = False
                    for selector in photo_selectors:
                        try:
                            elements = self.driver.find_elements(By.XPATH, selector)
                            if elements:
                                element = elements[0]
                                if element.is_displayed() and element.is_enabled():
                                    self._human_click(element)
                                    photo_clicked = True
                                    break
                        except Exception:
                            continue
                    
                    if photo_clicked:
                        time.sleep(random.uniform(1.0, 2.0))
                        # Tìm input file
                        file_inputs = self.driver.find_elements(By.XPATH, "//input[@type='file' and not(@disabled)]")
                        if file_inputs:
                            file_inputs[0].send_keys(os.path.abspath(image_path))
                            print("✅ Đã upload hình ảnh")
                            time.sleep(random.uniform(2.0, 3.0))
                        else:
                            print("⚠️ Không tìm thấy input file")
                    else:
                        print("⚠️ Không tìm thấy nút upload ảnh")
                except Exception as e:
                    print(f"⚠️ Lỗi khi upload ảnh: {e}")
            
            # Tìm và click nút Đăng
            post_selectors = [
                "//div[@role='dialog']//div[@role='button']//span[normalize-space(.)='Đăng']/ancestor::div[@role='button']",
                "//div[@role='dialog']//div[@role='button']//span[normalize-space(.)='Post']/ancestor::div[@role='button']",
                "//div[@role='dialog']//div[@role='button' and not(@aria-disabled='true')]",
            ]
            
            post_clicked = False
            for selector in post_selectors:
                try:
                    elements = self.driver.find_elements(By.XPATH, selector)
                    if elements:
                        element = elements[0]
                        if element.is_displayed() and element.is_enabled():
                            print(f"🔍 Tìm thấy nút đăng với selector: {selector}")
                            self._human_click(element)
                            post_clicked = True
                            break
                except Exception as e:
                    print(f"⚠️ Lỗi với post selector {selector}: {e}")
                    continue
            
            if not post_clicked:
                print("❌ Không tìm thấy nút đăng bài")
                return False
            
            # Đợi bài đăng được gửi
            print("⏳ Đang đợi bài đăng được gửi...")
            time.sleep(random.uniform(3.0, 5.0))
            
            # Kiểm tra xem dialog đã đóng chưa
            dialogs = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
            if not dialogs:
                print("✅ Dialog đã đóng - bài đăng có thể đã thành công")
                return True
            else:
                print("⚠️ Dialog vẫn mở - có thể bài đăng chưa thành công")
                return False
                
        except Exception as e:
            print(f"❌ Lỗi khi đăng bài: {e}")
            return False

    def like_first_posts(self, min_count=3, max_count=5):
        """
        Lướt feed từ đầu trang và bấm Thích 3–5 bài đầu tiên nếu thấy nút Like/Thích.
        """
        try:
            target = random.randint(min_count, max_count)
            print(f"👍 VISIT_LIKE: sẽ bấm Like {target} bài đầu tiên.")
            liked = 0

            # cuộn nhẹ để FB nạp feed
            self.driver.execute_script("window.scrollTo({top:0,behavior:'instant'});")
            time.sleep(random.uniform(0.8, 1.2))

            # Thu thập các bài (article) đầu trang
            def collect_articles():
                return self.driver.find_elements(By.XPATH, "//div[@role='article']")

            articles = collect_articles()
            # Nếu chưa đủ bài để đạt target, cuộn để nạp thêm
            tries_load = 0
            while len(articles) < target and tries_load < 3:
                self.driver.execute_script("window.scrollBy({top:1400,behavior:'smooth'});")
                time.sleep(random.uniform(1.0, 1.6))
                articles = collect_articles()
                tries_load += 1

            i = 0
            processed = set()
            # Duyệt và có thể cuộn thêm nếu chưa đạt target
            while i < len(articles) and liked < target:
                art = articles[i]
                i += 1
                if art.id in processed:
                    continue
                processed.add(art.id)
                if liked >= target:
                    break
                try:
                    # kéo vào giữa màn hình để hiện rõ nút
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", art)
                    time.sleep(random.uniform(0.4, 0.8))

                    # Tìm đúng nút Like của thanh tác vụ bài viết (không phải Like trong bình luận)
                    like_btn = None

                    # Các XPath selectors mạnh mẽ hơn để tìm nút Like
                    like_selectors = [
                        # Selector chính - tìm nút Like trong thanh tác vụ bài viết
                        ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like'])]",

                        # Selector cho nút Like với aria-pressed
                        ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like') and not(@aria-pressed='true')]",

                        # Selector tìm theo span text
                        ".//span[normalize-space(.)='Thích' or normalize-space(.)='Like']/ancestor::div[@role='button']",

                        # Selector tìm nút Like gần nút Comment/Share
                        ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like') and not(contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'comment') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bình luận'))]",

                        # Selector cho các nút reaction (bao gồm cả nút Like đã like)
                        ".//div[@role='button' and (@data-testid='fb-ufi-likelink' or @data-testid='like-button')]",

                        # Selector mới cho Facebook hiện đại
                        ".//div[@role='button' and contains(@class,'x1i10hfl') and (.//span[contains(text(),'Thích')] or .//span[contains(text(),'Like')])]",
                    ]

                    # Thử từng selector để tìm nút Like chưa được nhấn
                    for selector in like_selectors:
                        try:
                            candidates = art.find_elements(By.XPATH, selector)
                            # Lọc bỏ những nút đã được nhấn (aria-pressed='true')
                            available_candidates = [c for c in candidates if (c.get_attribute('aria-pressed') or '').lower() != 'true']

                            if available_candidates:
                                like_btn = available_candidates[0]
                                break
                        except Exception:
                            continue

                    # Nếu vẫn không tìm thấy, thử tìm bất kỳ nút Like nào và kiểm tra trạng thái sau
                    if like_btn is None:
                        for selector in like_selectors:
                            try:
                                candidates = art.find_elements(By.XPATH, selector)
                                if candidates:
                                    like_btn = candidates[0]
                                    break
                            except Exception:
                                continue

                    if like_btn:
                        # hover + chọn reaction (Yêu thích/Thích) để tránh miss-click
                        if not self._hover_and_react(like_btn, prefer=("love","like")):
                            # fallback click nếu không mở được khay reaction
                            self._human_click(like_btn)
                        liked += 1
                        print(f"   ✓ Đã Like {liked}/{target}")
                        time.sleep(random.uniform(0.8, 1.6))
                except Exception:
                    continue

                # Nếu đã xử lý hết danh sách mà chưa đạt mục tiêu → cuộn nạp thêm và tiếp tục
                if i >= len(articles) and liked < target:
                    self.driver.execute_script("window.scrollBy({top:1600,behavior:'smooth'});")
                    time.sleep(random.uniform(1.0, 1.8))
                    more = collect_articles()
                    # nối thêm các article mới
                    if len(more) > len(articles):
                        articles = more

            print(f"✅ VISIT_LIKE: hoàn tất, Like {liked} bài.")
        except Exception as e:
            print(f"❌ VISIT_LIKE: lỗi khi Like bài: {e}")

    # >>> ADD vào trong class UniversalFacebookPoster

    def run_visit_like(self, csv_path: str):
        print("🚀 Bắt đầu chế độ VISIT_LIKE")
        groups = self._read_groups_for_visit_like(csv_path)
        if not groups:
            print("❌ Không có group hợp lệ để VISIT_LIKE."); return

        # Trộn ngẫu nhiên thứ tự các group
        random.shuffle(groups)
        print(f"🔀 Đã trộn ngẫu nhiên thứ tự {len(groups)} nhóm.")

        self.login_facebook()

        for idx, g in enumerate(groups, 1):
            print("\n" + "#"*70)
            print(f"## VISIT_LIKE {idx}/{len(groups)}: {g['name']}")
            print("#"*70)
            try:
                self.driver.get(g["url"])
                time.sleep(random.uniform(3.0, 5.0))
                
                # Tải nội dung bài đăng mới cho mỗi nhóm
                content = ""
                if VISIT_LIKE_ENABLE_POST:
                    # Ưu tiên sử dụng nội dung từ posts/contents.txt
                    content = self.get_random_content_from_posts()
                    if not content:
                        # Fallback về file cũ nếu không có
                        content = self.load_post_content(DEFAULT_CONTENT_TXT)
                    if not content:
                        print("⚠️ Không có nội dung để đăng, chỉ thực hiện tương tác.")
                
                # Debug: In thông tin
                print(f"🔍 DEBUG: VISIT_LIKE_ENABLE_POST = {VISIT_LIKE_ENABLE_POST}")
                print(f"🔍 DEBUG: content length = {len(content) if content else 0}")
                print(f"🔍 DEBUG: VISIT_LIKE_POST_PROBABILITY = {VISIT_LIKE_POST_PROBABILITY}")
                
                # Đăng bài trước nếu được bật và có nội dung
                if VISIT_LIKE_ENABLE_POST and content and random.random() < VISIT_LIKE_POST_PROBABILITY:
                    print(f"📝 Thực hiện đăng bài (xác suất: {VISIT_LIKE_POST_PROBABILITY})")
                    try:
                        # Chọn ngẫu nhiên hình ảnh từ thư mục posts
                        random_image = self.get_random_image_from_posts()
                        image_path = random_image if random_image else DEFAULT_IMAGE_PATH
                        
                        print(f"🔍 DEBUG: image_path = {image_path}")
                        print(f"🔍 DEBUG: content preview = {content[:100]}...")
                        
                        # Sử dụng hàm đăng bài đơn giản
                        success = self.simple_post_to_group(content, image_path)
                        if success:
                            print("✅ Đã đăng bài thành công")
                        else:
                            print("⚠️ Không thể đăng bài, tiếp tục tương tác")
                    except Exception as e:
                        print(f"⚠️ Lỗi khi đăng bài: {e}")
                else:
                    if not VISIT_LIKE_ENABLE_POST:
                        print("ℹ️ Chế độ đăng bài đã tắt")
                    elif not content:
                        print("ℹ️ Không có nội dung để đăng")
                    else:
                        print(f"ℹ️ Bỏ qua đăng bài (xác suất: {VISIT_LIKE_POST_PROBABILITY})")
                
                # Thực hiện tương tác Like sau khi đăng bài
                self.like_first_posts(3, 5)
                        
            except KeyboardInterrupt:
                print("\n🛑 Dừng bởi người dùng."); break
            except Exception as e:
                print(f"⚠️ Lỗi tại group này: {e}")
            finally:
                # cập nhật LastUpdated ngay trước khi qua nhóm tiếp theo
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                self._write_last_updated(csv_path, g["row_index"], now)

            # nghỉ nhẹ giữa các nhóm
            delay = random.uniform(*DEFAULT_DELAY_RANGE)
            print(f"⏳ Nghỉ {delay:.1f}s trước nhóm tiếp theo…")
            time.sleep(delay)

        print("\n🎉 Hoàn tất VISIT_LIKE")


    # ---------------- WebDriver ----------------
    def setup_driver(self):
        print("🔧 Đang thiết lập WebDriver (persistent profile)...")

        # 1) Chuẩn bị profile cố định + giữ lock
        profile_dir, lock_fd, is_main = _prepare_persistent_profile()
        self._profile_dir = str(profile_dir)
        self._profile_lock_fd = lock_fd
        self._profile_is_main = is_main

        chrome_options = Options()

        # (Tùy chọn) chỉ định binary nếu cần:
        # os.environ["GOOGLE_CHROME_BIN"] = "/usr/bin/google-chrome-stable"
        chrome_bin = os.environ.get("GOOGLE_CHROME_BIN")
        if chrome_bin:
            chrome_options.binary_location = chrome_bin

        # 2) Bật user-data-dir profile cố định
        chrome_options.add_argument(f"--user-data-dir={self._profile_dir}")
        chrome_options.add_argument("--profile-directory=Default")

        # 3) Flags ổn định cho Linux
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--lang=vi-VN")
        chrome_options.add_experimental_option(
            "prefs",
            {
                "intl.accept_languages": "vi-VN,vi,en-US,en",
                "profile.default_content_setting_values.notifications": 2,
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                "autofill.profile_enabled": False,
            },
        )

        # 4) User-Agent “người thật”
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        )

        # 5) Headless (nếu muốn)
        if _env_bool("HEADLESS", default=False):
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1368,832")

        # 6) Remote debugging port riêng để tránh đụng nhau
        debug_port = _find_free_tcp_port(9222, 9550) or 0
        if debug_port:
            chrome_options.add_argument(f"--remote-debugging-port={debug_port}")

        # 7) Khởi tạo driver
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

        self.wait = WebDriverWait(self.driver, 20)
        prof_kind = "MAIN" if self._profile_is_main else "SECONDARY"
        print(f"✅ Chrome WebDriver sẵn sàng với profile [{prof_kind}]: {self._profile_dir}"
              f"{f' (debug-port={debug_port})' if debug_port else ''}")

    def close(self):
        """Đóng driver; giữ lại profile để duy trì cookies. Chỉ nhả file lock."""
        try:
            if getattr(self, "driver", None):
                self.driver.quit()
        except Exception:
            pass
        try:
            lock_fd = getattr(self, "_profile_lock_fd", None)
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                self._profile_lock_fd = None
        except Exception:
            pass



    def _load_cookies_from_file(self):
        cookies_json_path = os.path.join("data", "cookies.json")
        cookies_txt_path = os.path.join("data", "cookies.txt")
        
        # 1) Thử đọc JSON trước
        if os.path.exists(cookies_json_path):
            print(f"🍪 Tìm thấy file cookies JSON tại: {cookies_json_path}")
            try:
                import json
                with open(cookies_json_path, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                
                self.driver.delete_all_cookies()
                for c in cookies:
                    cookie_dict = {
                        "name": c.get("name"),
                        "value": c.get("value"),
                        "domain": c.get("domain", ".facebook.com"),
                        "path": c.get("path", "/"),
                    }
                    if "expiry" in c:
                        cookie_dict["expiry"] = int(c["expiry"])
                    elif "expires" in c:
                        cookie_dict["expiry"] = int(c["expires"])
                    
                    try:
                        self.driver.add_cookie(cookie_dict)
                    except Exception:
                        try:
                            cookie_dict.pop("domain", None)
                            self.driver.add_cookie(cookie_dict)
                        except Exception:
                            pass
                print("✅ Đã nạp thành công cookies từ JSON!")
                return True
            except Exception as e:
                print(f"❌ Lỗi khi đọc file cookies JSON: {e}")
                
        # 2) Thử đọc txt dạng chuỗi raw (c_user=xxxx; xs=xxxx; ...)
        elif os.path.exists(cookies_txt_path):
            print(f"🍪 Tìm thấy file cookies raw text tại: {cookies_txt_path}")
            try:
                with open(cookies_txt_path, 'r', encoding='utf-8') as f:
                    raw_cookie = f.read().strip()
                
                if not raw_cookie:
                    return False
                
                self.driver.delete_all_cookies()
                parts = raw_cookie.split(';')
                loaded_count = 0
                for part in parts:
                    part = part.strip()
                    if not part or '=' not in part:
                        continue
                    name, value = part.split('=', 1)
                    cookie_dict = {
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".facebook.com",
                        "path": "/"
                    }
                    try:
                        self.driver.add_cookie(cookie_dict)
                        loaded_count += 1
                    except Exception:
                        pass
                if loaded_count > 0:
                    print(f"✅ Đã nạp thành công {loaded_count} cookies từ raw text!")
                    return True
            except Exception as e:
                print(f"❌ Lỗi khi đọc file cookies raw text: {e}")
                
        return False

    def login_facebook(self):
        print("🔐 Đang mở Facebook...")
        self.driver.get("https://www.facebook.com")
        time.sleep(4)
        
        # Nạp cookie nếu file tồn tại
        if self._load_cookies_from_file():
            print("🔄 Đang áp dụng cookie, tải lại trang Facebook...")
            self.driver.get("https://www.facebook.com")
            time.sleep(4)

        is_logged_out = False
        try:
            # Kiểm tra xem có trường nhập email/sđt hoặc nút đăng nhập không
            if (self.driver.find_elements(By.NAME, "email") or 
                self.driver.find_elements(By.ID, "email") or 
                self.driver.find_elements(By.NAME, "login") or
                "login" in self.driver.current_url):
                is_logged_out = True
        except Exception:
            pass

        if is_logged_out:
            print("⚠️  Chưa đăng nhập hoặc phiên làm việc đã hết hạn.")
            print("👉 Vui lòng đăng nhập tài khoản Facebook của bạn trong cửa sổ trình duyệt Chrome vừa mở ra.")
            print("👉 Thực hiện xác thực 2FA (nếu có). Sau khi vào được bảng tin Facebook thành công...")
            input("📝 Hãy nhấn ENTER tại cửa sổ Terminal này để bắt đầu chạy tool...")
        else:
            print("✅ Đã đăng nhập Facebook!")

    # ---------------- I/O ----------------
    def load_groups(self, csv_file):
        groups = []
        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    if not row.get('url'):
                        continue
                    groups.append({'name': row.get('name', row['url']), 'url': row['url']})
            print(f"✅ Đã tải {len(groups)} group từ {csv_file}")
        except Exception as e:
            print(f"❌ Lỗi khi tải danh sách group: {e}")
        return groups

    def load_post_content(self, content_file):
        try:
            with open(content_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            print("✅ Đã tải nội dung bài đăng")
            return content
        except Exception as e:
            print(f"❌ Lỗi khi tải nội dung: {e}")
            return ""

    def get_random_image_from_posts(self):
        """Lấy ngẫu nhiên một hình ảnh từ thư mục posts"""
        try:
            import glob
            image_extensions = ['*.jpg', '*.jpeg', '*.png', '*.gif', '*.bmp', '*.webp']
            image_files = []
            
            for ext in image_extensions:
                pattern = os.path.join(POSTS_DIR, ext)
                image_files.extend(glob.glob(pattern))
                # Cũng tìm trong thư mục con
                pattern = os.path.join(POSTS_DIR, '**', ext)
                image_files.extend(glob.glob(pattern, recursive=True))
            
            if image_files:
                selected_image = random.choice(image_files)
                print(f"🖼️ Chọn hình ảnh ngẫu nhiên: {selected_image}")
                return selected_image
            else:
                print(f"⚠️ Không tìm thấy hình ảnh nào trong thư mục {POSTS_DIR}")
                return None
        except Exception as e:
            print(f"❌ Lỗi khi tìm hình ảnh: {e}")
            return None

    def get_random_content_from_posts(self):
        """Lấy ngẫu nhiên một nội dung bài đăng từ file contents.txt"""
        try:
            with open(POSTS_CONTENTS_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # Tách các bài đăng theo dấu # (số thứ tự)
            posts = []
            current_post = []
            
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('#') and (line.startswith('#1:') or line.startswith('#2:') or 
                                           line.startswith('#3:') or line.startswith('#4:') or 
                                           line.startswith('#5:') or line.startswith('#6:') or 
                                           line.startswith('#7:') or line.startswith('#8:') or 
                                           line.startswith('#9:') or line.startswith('#10:') or
                                           line.startswith('#11:') or line.startswith('#12:') or
                                           line.startswith('#13:') or line.startswith('#14:') or
                                           line.startswith('#15:') or line.startswith('#16:') or
                                           line.startswith('#17:') or line.startswith('#18:') or
                                           line.startswith('#19:') or line.startswith('#20:')):
                    # Bắt đầu bài đăng mới
                    if current_post:
                        posts.append('\n'.join(current_post).strip())
                    current_post = [line]
                elif line and not line.startswith('#Top 20'):
                    # Thêm dòng vào bài đăng hiện tại
                    current_post.append(line)
            
            # Thêm bài đăng cuối cùng
            if current_post:
                posts.append('\n'.join(current_post).strip())
            
            if posts:
                selected_content = random.choice(posts)
                print(f"📝 Chọn nội dung ngẫu nhiên: {selected_content[:50]}...")
                return selected_content
            else:
                print("⚠️ Không tìm thấy bài đăng nào trong file contents.txt")
                return ""
        except Exception as e:
            print(f"❌ Lỗi khi tải nội dung từ posts: {e}")
            return ""

    # ===== Beautify & editor helpers (giữ nguyên code cũ) =====
    def _to_math_bold(self, s: str) -> str:
        res = []
        for ch in s:
            o = ord(ch)
            if 'A' <= ch <= 'Z':
                res.append(chr(0x1D400 + (o - 65)))
            elif 'a' <= ch <= 'z':
                res.append(chr(0x1D41A + (o - 97)))
            elif '0' <= ch <= '9':
                res.append(chr(0x1D7CE + (o - 48)))
            else:
                res.append(ch)
        return "".join(res)

    def _beautify_content(self, raw: str) -> str:
        import re
        txt = (raw or "").replace("\r\n", "\n").replace("\r", "\n")

        def _ensure_clickable_urls(line:str)->str:
            pat = re.compile(r'(?:www\.)?cdha\.ai(?:/[^\s]*)?')
            def repl(m):
                i = m.start(); s=line
                if (i>=7 and s[i-7:i].lower()=='http://') or (i>=8 and s[i-8:i].lower()=='https://'): return m.group(0)
                if i>=1 and s[i-1]=='@': return m.group(0)
                return 'https://' + m.group(0)
            out = pat.sub(repl, line)
            out = re.sub(r'https?://https?://','https://',out,flags=re.IGNORECASE)
            return out

        lines = [_ensure_clickable_urls(ln.rstrip()) for ln in txt.split("\n")]

        h_idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
        if h_idx is not None and ENABLE_BEAUTIFY:
            lines[h_idx] = "⭐ " + self._to_math_bold(lines[h_idx].strip())

        cleaned = []
        for ln in lines:
            s = ln.strip()
            if s == "." or (set(s) <= set("-—–._•") and len(s) >= 3):
                cleaned.append("")
            else:
                cleaned.append(ln)
        lines = cleaned

        if BULLET_CONTACT:
            for i, ln in enumerate(lines):
                s = ln.strip()
                if not s: continue
                lower = s.lower()
                if lower.startswith("email:") or lower.startswith("website:"):
                    if not s.startswith(("•", "-", "*", "—")):
                        lines[i] = "• " + s

        out, blank = [], False
        for ln in lines:
            if ln.strip() == "":
                if not blank:
                    out.append("")
                    blank = True
            else:
                out.append(ln)
                blank = False
        return "\n".join(out).strip("\n")

    # ===== Human-like mouse, interactions (giữ nguyên code cũ) =====
    def _ease(self, t: float) -> float:
        return 4*t*t*t if t < 0.5 else 1 - pow(-2*t+2, 3)/2

    def _rand(self, a, b):
        return random.uniform(a, b)

    def _element_center_in_viewport(self, el):
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        rect = self.driver.execute_script("""
            const r = arguments[0].getBoundingClientRect();
            return {x: r.left + r.width/2, y: r.top + r.height/2, w:r.width, h:r.height};
        """, el)
        jx = self._rand(-min(6, rect["w"]/6), min(6, rect["w"]/6))
        jy = self._rand(-min(6, rect["h"]/6), min(6, rect["h"]/6))
        return rect["x"] + jx, rect["y"] + jy

    def _human_move_to_xy(self, x: int, y: int, duration: float = None):
        if not ENABLE_HUMAN_MOUSE:
            return
        if duration is None:
            duration = self._rand(0.2, 0.6)
        steps = max(12, int(duration * 60))
        cur = self.driver.execute_script("""
            if(!window.__mouse){ window.__mouse = {x: 20, y: 20}; }
            return window.__mouse;
        """)
        x0, y0 = float(cur["x"]), float(cur["y"])
        cx1 = x0 + self._rand(-80, 80)
        cy1 = y0 + self._rand(40, 120)
        cx2 = x + self._rand(-80, 80)
        cy2 = y - self._rand(40, 120)

        actions = ActionBuilder(self.driver)
        mouse = PointerInput(PointerInput.INTERACTION_MOUSE, "mouse")
        actions.add_action(mouse)

        for i in range(1, steps + 1):
            t = i / steps
            e = self._ease(t)
            bx = (1-e)**3 * x0 + 3*(1-e)**2 * e * cx1 + 3*(1-e) * e**2 * cx2 + e**3 * x
            by = (1-e)**3 * y0 + 3*(1-e)**2 * e * cy1 + 3*(1-e) * e**2 * cy2 + e**3 * y
            actions.pointer_action.move_to_location(int(bx), int(by))
        actions.perform()
        self.driver.execute_script("window.__mouse = {x: arguments[0], y: arguments[1]};", int(x), int(y))
        time.sleep(self._rand(0.03, 0.09))

    def _human_click(self, el, button="left"):
        try:
            x, y = self._element_center_in_viewport(el)
            self._human_move_to_xy(int(x), int(y))
        except Exception:
            try:
                el.click(); return
            except Exception:
                self.driver.execute_script("arguments[0].click();", el); return

        if ENABLE_HUMAN_MOUSE:
            for _ in range(2):
                self._human_move_to_xy(int(x + self._rand(-2,2)), int(y + self._rand(-2,2)))

        actions = ActionBuilder(self.driver)
        mouse = PointerInput(PointerInput.INTERACTION_MOUSE, "mouse")
        actions.add_action(mouse)
        actions.pointer_action.pointer_down()
        actions.pointer_action.pause(self._rand(0.03, 0.08))
        actions.pointer_action.pointer_up()
        actions.perform()
        time.sleep(self._rand(0.05, 0.12))

    def _slow_scroll_page(self, sections=3):
        try:
            for _ in range(sections):
                dy = random.randint(400, 1200)
                self.driver.execute_script(f"window.scrollBy({{top:{dy},behavior:'smooth'}});")
                time.sleep(random.uniform(0.6, 1.5))
            if random.random() < 0.35:
                self.driver.execute_script("window.scrollBy({top:-300,behavior:'smooth'});")
                time.sleep(random.uniform(0.4, 1.0))
        except Exception:
            pass

    def _open_group_tab(self, labels):
        try:
            xps = []
            for lb in labels:
                lb_n = lb.replace("'", "\\'")
                xps += [
                    f"//a[@role='tab' and contains(normalize-space(.), '{lb_n}')]",
                    f"//div[@role='tab' and contains(normalize-space(.), '{lb_n}')]",
                    f"//a[contains(@href,'/about') and contains(normalize-space(.), '{lb_n}')]",
                    f"//a[contains(normalize-space(.), '{lb_n}')]",
                    f"//*[contains(@role,'tab') and contains(normalize-space(.), '{lb_n}')]"
                ]
            els = []
            for xp in xps:
                els = self.driver.find_elements(By.XPATH, xp)
                if els: break
            if els:
                el = els[0]
                self._human_click(el) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", el)
                time.sleep(random.uniform(1.0, 2.2))
                return True
        except Exception:
            pass
        return False

    def _open_random_post_then_back(self):
        try:
            candidates = self.driver.find_elements(
                By.XPATH,
                "//a[contains(@href,'/posts/') or contains(@href,'/permalink/') or contains(@href,'/videos/')]"
            )
            if not candidates:
                articles = self.driver.find_elements(By.XPATH, "//article")
                if articles:
                    target = random.choice(articles)
                    link = target.find_elements(By.XPATH, ".//a[contains(@href,'/posts/') or contains(@href,'/videos/') or contains(@href,'/permalink/')]")
                    if link:
                        candidates = link
            if candidates:
                el = random.choice(candidates[:10])
                self._human_click(el) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", el)
                time.sleep(random.uniform(1.2, 3.0))
                self.driver.back()
                time.sleep(random.uniform(0.8, 1.5))
                return True
        except Exception:
            pass
        return False

    def _click_random_like(self):
        if random.random() > LIKE_PROBABILITY:
            return False
        try:
            # Chỉ Like ở thanh tác vụ bài viết: khối có cả Bình luận/Comment và Thích/Like
            block_xp = (
                "//div[@role='article']//div[.//div[@role='button' and (@aria-label='Bình luận' or @aria-label='Comment' or .//span[normalize-space(.)='Bình luận'] or .//span[normalize-space(.)='Comment'])] "
                "and .//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like'])]]"
            )
            blocks = self.driver.find_elements(By.XPATH, block_xp)
            like_candidates = []
            for blk in blocks:
                cands = blk.find_elements(
                    By.XPATH,
                    ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like']) and not(@aria-pressed='true')]"
                )
                like_candidates.extend(cands)

            # Fallback nhẹ nếu chưa có, nhưng loại vùng bình luận
            if not like_candidates:
                fb = self.driver.find_elements(By.XPATH, "//div[@role='article']//div[@role='button' and (@aria-label='Thích' or @aria-label='Like') and not(ancestor::*[@aria-label='Bình luận' or @aria-label='Viết bình luận' or @aria-label='Write a comment'])]")
                like_candidates = fb

            if like_candidates:
                el = random.choice(like_candidates[:5])
                ap = (el.get_attribute('aria-pressed') or '').lower()
                if ap == 'true':
                    return False
                # Hover + chọn reaction
                if not self._hover_and_react(el, prefer=("love","like")):
                    self._human_click(el) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", el)
                time.sleep(random.uniform(0.6, 1.3))
                return True
        except Exception:
            pass
        return False

    def _open_random_media_grid(self):
        if not self._open_group_tab(['Ảnh','Photos','Photo','Media','Phương tiện','Video','Videos']):
            return False
        try:
            ths = self.driver.find_elements(By.XPATH, "//a[contains(@href,'photo') or contains(@href,'video') or descendant::img]")
            if ths:
                el = random.choice(ths[:12])
                self._human_click(el) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", el)
                time.sleep(random.uniform(1.2, 2.5))
                self.driver.back()
                time.sleep(random.uniform(0.8, 1.5))
            self._open_group_tab(['Bài viết','Discussion','Bài đăng','Posts'])
            return True
        except Exception:
            return False

    def _open_about_or_members(self):
        opened = self._open_group_tab(['Giới thiệu','About']) if random.random() < 0.6 \
                 else self._open_group_tab(['Thành viên','Members'])
        if opened:
            self._slow_scroll_page(sections=random.randint(2, 4))
            self._open_group_tab(['Bài viết','Discussion','Bài đăng','Posts'])
            return True
        return False

    def perform_human_interactions(self, count=None):
        if count is None:
            count = random.randint(*INTERACTION_COUNT_RANGE)
        actions = [
            lambda: self._slow_scroll_page(sections=random.randint(2, 4)),
            lambda: self._open_about_or_members(),
            lambda: self._open_random_media_grid(),
            lambda: self._open_random_post_then_back(),
            lambda: self._click_random_like(),
        ]
        random.shuffle(actions)
        picked = actions[:count]
        print(f"🤹 Đang chèn {len(picked)} tương tác tự nhiên...")
        for act in picked:
            try:
                act()
            except Exception:
                pass
            time.sleep(random.uniform(0.4, 1.2))

    # ============ Helpers phát hiện trạng thái group ============
    def _is_member_already(self) -> bool:
        """
        Coi như đã là member nếu thấy nút/nhãn kiểu 'Joined', 'Đã tham gia', 'Member', hoặc không thấy nút 'Tham gia nhóm'.
        """
        try:
            # Tín hiệu 'Joined'
            joined_markers = [
                "//span[normalize-space(.)='Đã tham gia']",
                "//span[normalize-space(.)='Joined']",
                "//span[normalize-space(.)='Thành viên']",
                "//div[@aria-label='Đã tham gia']",
            ]
            for xp in joined_markers:
                if self.driver.find_elements(By.XPATH, xp):
                    return True

            # Nếu có nút Join → chưa phải member
            join_btns = self._find_join_buttons()
            if join_btns:
                return False

            # Không có Join, không có dấu 'Đã tham gia' — có thể là member (FB giao diện thay đổi)
            return True
        except Exception:
            return False

    def _find_join_buttons(self):
        xps = [
            "//div[@role='button']//span[normalize-space(.)='Tham gia nhóm']/ancestor::div[@role='button']",
            "//div[@role='button']//span[normalize-space(.)='Join group']/ancestor::div[@role='button']",
            "//span[normalize-space(.)='Tham gia']/ancestor::div[@role='button']",
            "//span[normalize-space(.)='Join']/ancestor::div[@role='button']",
        ]
        for xp in xps:
            btns = self.driver.find_elements(By.XPATH, xp)
            if btns:
                return btns
        return []

    # ============ Trả lời câu hỏi & đồng ý nội quy ============
    def _extract_group_title(self) -> str:
        try:
            # Header group title
            candidates = self.driver.find_elements(By.XPATH, "//h1//span|//h1|//h2")
            for el in candidates[:3]:
                text = el.text.strip()
                if 2 <= len(text) <= 120:
                    return text
        except Exception:
            pass
        return ""

    def _answer_join_questions_and_rules(self) -> str:
        """
        Điền câu trả lời cho form gia nhập (nếu có).
        Trả về 'answered' nếu đã điền và gửi, 'no_questions' nếu không thấy form, hoặc 'error:...'
        """
        try:
            # Sau khi bấm Join, Facebook có thể mở sheet/dialog câu hỏi
            time.sleep(random.uniform(1.0, 2.0))

            # Tìm container của form
            form = None
            possible = [
                "//div[@role='dialog']",
                "//div[contains(@class,'x1e56ztr') and descendant::textarea or descendant::input]",
                "//form[.//textarea or .//input[@type='text']]",
            ]
            for xp in possible:
                els = self.driver.find_elements(By.XPATH, xp)
                if els:
                    form = els[0]; break
            if not form:
                print("ℹ️  Không thấy dialog câu hỏi/nội quy → có thể nhóm auto-approve hoặc không đặt câu hỏi.")
                return "no_questions"

            group_title = self._extract_group_title()
            lang_hint = "auto"

            # Textareas
            textareas = form.find_elements(By.XPATH, ".//textarea[@aria-label or @name or @placeholder]")
            for ta in textareas[:5]:
                try:
                    q_label = ""
                    # cố gắng lấy câu hỏi
                    lab_els = ta.find_elements(By.XPATH, ".//ancestor::div[1]//label|.//ancestor::div[2]//label")
                    if lab_els:
                        q_label = lab_els[0].text.strip()
                    if not q_label:
                        # thử sibling text
                        par_text = self.driver.execute_script("return arguments[0].parentElement?.innerText || '';", ta).strip()
                        q_label = (par_text or "Why do you want to join?")[:140]

                    ans = ai_answer(q_label, group_title, DEFAULT_DOMAIN, lang_hint)
                    self._human_click(ta)
                    time.sleep(0.3)
                    ta.clear()
                    ta.send_keys(ans)
                    time.sleep(0.4)
                except Exception:
                    continue

            # Input text
            inputs = form.find_elements(By.XPATH, ".//input[@type='text' and @aria-label or @name or @placeholder]")
            for inp in inputs[:5]:
                try:
                    q_label = self.driver.execute_script("return arguments[0].placeholder || arguments[0].ariaLabel || '';", inp) or ""
                    if not q_label:
                        q_label = "Your answer"
                    ans = ai_answer(q_label, group_title, DEFAULT_DOMAIN, lang_hint)
                    self._human_click(inp)
                    time.sleep(0.2)
                    inp.clear()
                    inp.send_keys(ans[:120])
                    time.sleep(0.3)
                except Exception:
                    continue

            # Checkbox đồng ý nội quy
            # Tìm checkbox + label có chữ agree/đồng ý/rule
            agree_candidates = form.find_elements(
                By.XPATH,
                ".//label[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'agree') or "
                "contains(., 'đồng ý') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'rules') or "
                "contains(., 'nội quy') or contains(., 'quy định') or contains(., 'tuân thủ')]"
            )
            for lb in agree_candidates[:3]:
                try:
                    cb = None
                    # Tìm input checkbox trong label hoặc gần đó
                    cbs = lb.find_elements(By.XPATH, ".//input[@type='checkbox']")
                    if cbs:
                        cb = cbs[0]
                    else:
                        cbs = lb.find_elements(By.XPATH, ".//ancestor::div[1]//input[@type='checkbox']|.//following::input[@type='checkbox'][1]")
                        if cbs:
                            cb = cbs[0]
                    if cb:
                        self._human_click(cb)
                        time.sleep(0.2)
                except Exception:
                    pass

            # Radio/Multiple choice — chọn phương án “đồng ý” nếu thấy
            radios = form.find_elements(By.XPATH, ".//input[@type='radio' or @role='radio']")
            for rd in radios[:6]:
                try:
                    lbl = rd.get_attribute("aria-label") or ""
                    t = (lbl or "").lower()
                    if any(k in t for k in ["agree", "đồng ý", "yes", "i will", "accept"]):
                        self._human_click(rd)
                        time.sleep(0.2)
                except Exception:
                    pass

            # Nút Gửi/Yêu cầu tham gia
            submit_xps = [
                ".//div[@role='button']//span[normalize-space(.)='Gửi']/ancestor::div[@role='button']",
                ".//div[@role='button']//span[normalize-space(.)='Submit']/ancestor::div[@role='button']",
                ".//div[@role='button']//span[normalize-space(.)='Gửi câu trả lời']/ancestor::div[@role='button']",
                ".//div[@role='button']//span[normalize-space(.)='Yêu cầu tham gia']/ancestor::div[@role='button']",
                ".//div[@role='button']//span[contains(normalize-space(.),'Join')]/ancestor::div[@role='button']",
                ".//div[@role='button' and not(@aria-disabled='true')]",
                ".//button[not(@disabled)]",
            ]
            clicked = False
            for xp in submit_xps:
                btns = form.find_elements(By.XPATH, xp)
                if btns:
                    try:
                        self._human_click(btns[0]) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", btns[0])
                        clicked = True
                        time.sleep(random.uniform(1.0, 2.0))
                        break
                    except Exception:
                        continue
            if not clicked:
                print("⚠️  Không tìm thấy nút gửi form xin vào nhóm.")
                return "error:no_submit"

            # Chờ dialog đóng
            t0 = time.time()
            while time.time() - t0 < 12:
                dlg = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
                if not dlg:
                    break
                time.sleep(0.6)

            return "answered"
        except Exception as e:
            return f"error:{e}"

    # ============ Thao tác Join ============
    def join_group_flow(self) -> JoinResult:
        """
        Thực hiện bấm Join và xử lý Q&A/rules nếu có.
        Kết quả:
        - status: 'already_member' | 'request_sent' | 'joined' | 'error'
        - detail: chuỗi mô tả ngắn
        """
        try:
            if self._is_member_already():
                return JoinResult(status="already_member", detail="Đã là thành viên.")

            # Tìm & bấm Join
            join_btns = self._find_join_buttons()
            if not join_btns:
                return JoinResult(status="error", detail="Không tìm thấy nút Join/Tham gia.")
            self._human_click(join_btns[0]) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", join_btns[0])
            time.sleep(random.uniform(1.0, 2.0))

            # Trả lời Q&A / tick rules nếu có
            res = self._answer_join_questions_and_rules()
            if res.startswith("error"):
                return JoinResult(status="error", detail=res)

            # Sau khi gửi, thường là 'Đã gửi yêu cầu'
            # Kiểm tra xem có chuyển sang trạng thái 'Đã gửi yêu cầu' / 'Requested'
            requested_markers = [
                "//span[contains(normalize-space(.),'Đã gửi yêu cầu')]",
                "//span[contains(normalize-space(.),'Requested')]",
                "//div[contains(.,'request sent') or contains(.,'đã gửi yêu cầu')]",
            ]
            for xp in requested_markers:
                if self.driver.find_elements(By.XPATH, xp):
                    return JoinResult(status="request_sent", detail="Đã gửi yêu cầu tham gia (chờ duyệt).")

            # Một số nhóm auto-approve → trở thành member ngay
            if self._is_member_already():
                return JoinResult(status="joined", detail="Đã tham gia thành công (auto-approve).")

            # Nếu không chắc, coi như request đã gửi
            return JoinResult(status="request_sent", detail=f"Đã thực hiện Join (res={res}).")

        except Exception as e:
            return JoinResult(status="error", detail=str(e))

    # ============ CSV helpers cho TASK4JOIN ============
    def _read_join_tasks(self, csv_path: str):
        """
        Đọc task4join.csv — cột 0:STT, cột 1:name, cột 2:url, cột 3:notes (optional), cột 4:Status
        Trả về list[dict]: {stt,name,url,notes,status,row_index}
        CHỈ lấy các dòng có STT trong khoảng 01..99 (tức 1..99).
        """
        def _to_int_or_none(x: str):
            try:
                x = (x or "").strip()
                if x == "":
                    return None
                # chấp nhận "01", "02", ..."99" → int 1..99
                return int(x)
            except Exception:
                return None

        tasks = []
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if not rows:
                return tasks

            header = rows[0]
            has_header = False
            # phát hiện header thô sơ: nếu có từ 'url' trong hàng đầu
            joined = [str(c).lower() for c in header]
            if any("url" in c for c in joined):
                has_header = True

            start_idx = 1 if has_header else 0
            for i in range(start_idx, len(rows)):
                row = rows[i]
                if not row:
                    continue

                stt_raw = (row[0] if len(row) > 0 else "").strip()
                name    = (row[1] if len(row) > 1 else "").strip()
                url     = (row[2] if len(row) > 2 else "").strip()
                status   = (row[3] if len(row) > 3 else "").strip()
                #status  = (row[4] if len(row) > 4 else "").strip()

                # ---- LỌC STT 01..99 (1..99) ----
                stt_int = _to_int_or_none(stt_raw)
                if stt_int is None or not (1 <= stt_int <= 1000):
                    # BỎ QUA mọi dòng không thuộc 01..99
                    continue

                # ---- BỎ QUA nếu Status đã hoàn tất/joined/requested ----
                norm_status = self._norm_status(status)
                if norm_status in ("request_sent", "already_member", "joined"):
                    continue

                if url:
                    tasks.append({
                        "stt": stt_int,
                        "name": name or url,
                        "url": url,
                        "status": norm_status,   # lưu bản đã chuẩn hoá
                        "row_index": i
                    })

        except Exception as e:
            print(f"❌ Lỗi đọc {csv_path}: {e}")
        return tasks



    def _write_join_status(self, csv_path: str, row_index: int, new_status: str):
        """
        Cập nhật cột thứ 4 (Status) cho hàng row_index.
        Giữ nguyên các cột còn lại; nếu file có header, row_index đã là index thật trong file.
        """
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            # Đảm bảo đủ cột
            if row_index < 0 or row_index >= len(rows):
                print(f"⚠️  row_index {row_index} ngoài phạm vi khi ghi CSV.")
                return
            row = rows[row_index]
            while len(row) < 4:
                row.append("")
            row[3] = new_status
            rows[row_index] = row

            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerows(rows)
            print(f"📝 Đã ghi Status='{new_status}' vào dòng {row_index} của {csv_path}")
        except Exception as e:
            print(f"❌ Lỗi ghi trạng thái CSV: {e}")

    # ============ Luồng JOIN_BY_LIST ============
    def run_join_by_list(self, csv_path: str):
        print("🚀 Bắt đầu chế độ JOIN_BY_LIST")
        tasks = self._read_join_tasks(csv_path)
        # chỉ giữ lại các task đã có stt trong [1..99] (phòng khi sau này đổi logic đọc)
        # tasks = [t for t in tasks if isinstance(t.get("stt"), int) and 1 <= t["stt"] <= 99]

        if not tasks:
            print("❌ Không có dòng nào trong task4join.csv"); return

        # Đăng nhập FB
        self.login_facebook()

        # Lọc các dòng chưa có Status hoặc Status không phải 'joined'/'already_member'/'request_sent'
        # pending = [t for t in tasks if t.get("status", "").strip().lower() not in ("joined", "already_member", "request_sent")]
        # Lọc các dòng chưa hoàn tất
        pending = [t for t in tasks if self._norm_status(t.get("status", "")) not in ("joined", "already_member", "request_sent")]


        total = len(pending)
        print(f"📋 Có {total} nhóm cần xử lý join.")

        for idx, t in enumerate(pending, 1):
            name = t["name"]; url = t["url"]
            print("\n" + "#"*70)
            print(f"##   JOIN {idx}/{total}: {name}   ##")
            print("#"*70)

            try:
                self.driver.get(url)
                time.sleep(random.uniform(3.0, 5.0))

                # Nếu đã là member → cập nhật & tương tác tự nhiên (không post)
                if self._is_member_already():
                    print("✅ Đã là thành viên. Bắt đầu tương tác tự nhiên (không đăng bài).")
                    self.perform_human_interactions()
                    self._write_join_status(csv_path, t["row_index"], "already_member")
                else:
                    # Thực hiện join
                    jr = self.join_group_flow()
                    print(f"📦 Kết quả: {jr.status} — {jr.detail}")
                    self._write_join_status(csv_path, t["row_index"], jr.status)

                    # Nếu vừa joined thành công (auto-approve) → tương tác nhẹ
                    if jr.status == "joined":
                        self.perform_human_interactions()

            except KeyboardInterrupt:
                print("\n🛑 Dừng bởi người dùng.")
                break
            except Exception as e:
                print(f"❌ Lỗi khi xử lý {name}: {e}")
                self._write_join_status(csv_path, t["row_index"], f"error:{e}")

            # Nghỉ giữa mỗi nhóm
            delay = random.uniform(*DEFAULT_DELAY_RANGE)
            print(f"⏳ Nghỉ {delay:.1f}s trước nhóm tiếp theo…")
            time.sleep(delay)

        print("\n" + "="*70)
        print("🎉 HOÀN TẤT CHẾ ĐỘ JOIN_BY_LIST")
        print("="*70)

    # ============ (Giữ nguyên) post_to_group và runner cũ ============
    def _group_restricted(self):
        markers = [
            "Chỉ quản trị viên mới có thể đăng",
            "Only admins can post",
            "Nhóm đã tắt tính năng đăng bài",
            "Bài viết đã bị tắt",
            "Tính năng đăng đã bị tắt",
        ]
        for txt in markers:
            if self.driver.find_elements(By.XPATH, f"//*[contains(normalize-space(.), '{txt}')]"):
                return True
        return False

    def _clear_editor(self, el):
        self.driver.execute_script("""
            const el = arguments[0];
            el.focus();
            try{
              const sel = window.getSelection();
              const range = document.createRange();
              range.selectNodeContents(el);
              sel.removeAllRanges(); sel.addRange(range);
              document.execCommand('delete', false, null);
            }catch(e){}
        """, el)

    def _insert_text_at_caret_js(self, text):
        self.driver.execute_script("""
            const text = arguments[0] ?? "";
            let ok = false;
            if (document.execCommand) {
              try { ok = document.execCommand('insertText', false, text); } catch(e){}
            }
            if (!ok) {
              const sel = window.getSelection();
              if (sel && sel.rangeCount) {
                sel.deleteFromDocument();
                const node = document.createTextNode(text);
                const range = sel.getRangeAt(0);
                range.insertNode(node);
                range.setStartAfter(node); range.collapse(true);
                sel.removeAllRanges(); sel.addRange(range);
              }
            }
        """, text)

    def _type_multiline_js_plus_enter(self, el, text: str):
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self._clear_editor(el)
        self.driver.execute_script("arguments[0].focus();", el)
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                self._insert_text_at_caret_js(line)
            if i < len(lines) - 1:
                el.send_keys(Keys.ENTER)

    def _scroll_to_top(self):
        try:
            self.driver.execute_script("window.scrollTo({top:0,behavior:'instant'});")
            time.sleep(0.2)
        except Exception:
            pass

    def _open_group_composer(self):
        self._scroll_to_top()
        candidates = [
            (By.CSS_SELECTOR, "[data-pagelet^='GroupInlineComposer'] [role='button']"),
            (By.XPATH, "//span[contains(normalize-space(.),'Bạn viết gì')]/ancestor::div[@role='button']"),
            (By.XPATH, "//span[contains(normalize-space(.),\"What's on your mind\")]/ancestor::div[@role='button']"),
        ]
        for by, sel in candidates:
            try:
                btn = self.wait.until(EC.element_to_be_clickable((by, sel)))
                self._human_click(btn) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", btn)
                self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']")))
                return True
            except (TimeoutException, StaleElementReferenceException):
                continue
        return False

    def _find_post_button_candidates(self):
        return [
            (By.XPATH, "//div[@role='dialog']//div[@aria-disabled='false']//span[normalize-space(.)='Đăng']/ancestor::div[@role='button']"),
            (By.XPATH, "//div[@role='dialog']//div[@aria-disabled='false']//span[normalize-space(.)='Post']/ancestor::div[@role='button']"),
            (By.CSS_SELECTOR, "div[role='dialog'] div[aria-disabled='false'] [data-testid='react-composer-post-button']"),
            (By.CSS_SELECTOR, "div[role='dialog'] [data-testid='comet-post-button']"),
            (By.XPATH, "//div[@role='dialog']//div[@role='button' and not(@aria-disabled='true')]"
                       "[.//span and (contains(., 'Đăng') or contains(., 'Post'))]"),
        ]

    def _click_post_button(self):
        time.sleep(random.uniform(0.8, 2.2))
        for by, sel in self._find_post_button_candidates():
            try:
                btn = WebDriverWait(self.driver, 7).until(EC.element_to_be_clickable((by, sel)))
                print("🖱️  Tìm thấy nút Đăng — đang bấm…")
                self._human_click(btn) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", btn)
                return True
            except Exception:
                continue
        print("⚠️  Không tìm thấy nút 'Đăng/Post'.")
        return False

    def _wait_post_published(self, timeout=25):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                dialogs = self.driver.find_elements(By.XPATH, "//div[@role='dialog']")
                if not dialogs:
                    print("✅ Hộp thoại đã đóng → có thể bài viết đã đăng.")
                    return True
            except Exception:
                pass
            try:
                toasts = self.driver.find_elements(By.XPATH,
                    "//*[contains(normalize-space(.),'Bài viết của bạn đã được đăng') or "
                    "contains(normalize-space(.),'Your post has been published') or "
                    "contains(normalize-space(.),'Xem bài viết') or "
                    "contains(normalize-space(.),'View post')]")
                if toasts:
                    print("✅ Phát hiện thông báo/‘Xem bài viết’.")
                    return True
            except Exception:
                pass
            time.sleep(0.8)
        print("⏱️  Hết thời gian chờ đăng (có thể vẫn đang xử lý nền).")
        return False

    def _check_pending_approval(self):
        try:
            pending_posts = self.driver.find_elements(
                By.XPATH, "//*[contains(normalize-space(.), 'Đang chờ quản trị viên phê duyệt')]"
            )
            return len(pending_posts) > 0
        except Exception:
            return False

    def _comment_on_welcome_posts(self):
        try:
            print("👋 Đang tìm bài viết chào mừng để bình luận...")
            welcome_posts = self.driver.find_elements(
                By.XPATH, "//div[@role='article' and .//*[contains(normalize-space(.), 'Chào mừng các thành viên mới!')]]"
            )

            if not welcome_posts:
                print("🤷 Không tìm thấy bài viết chào mừng nào.")
                return False

            post_to_comment = welcome_posts[0]
            print("🔍 Kiểm tra xem đã bình luận trên bài này chưa...")
            my_previous_comments = post_to_comment.find_elements(
                By.XPATH, ".//*[contains(normalize-space(.), 'Cảm ơn Admin.')]"
            )
            if my_previous_comments:
                print("👍 Đã có bình luận cũ. Bỏ qua.")
                return False

            self.driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", post_to_comment)
            time.sleep(1.5)
            comment_box = post_to_comment.find_element(
                By.XPATH, ".//div[(@aria-label='Viết bình luận...' or @aria-label='Write a comment...') and @role='textbox']"
            )
            self._human_click(comment_box) if ENABLE_HUMAN_MOUSE else comment_box.click()
            time.sleep(random.uniform(1.2, 2.5))
            comment_box.send_keys("Cảm ơn Admin.")
            time.sleep(random.uniform(0.8, 1.5))
            comment_box.send_keys(Keys.ENTER)
            print("💬 Đã bình luận 'Cảm ơn Admin.'")
            time.sleep(random.uniform(2.5, 4.0))
            return True

        except (NoSuchElementException, TimeoutException):
            return False
        except Exception as e:
            print(f"⚠️ Lỗi khi bình luận bài viết chào mừng: {e}")
            return False

    def _like_random_posts_if_not_liked(self):
        try:
            num_to_like = random.randint(3, 5)
            print(f"👍 Bắt đầu Like ngẫu nhiên (mục tiêu: {num_to_like}).")
            self._slow_scroll_page(sections=random.randint(1, 2))
            all_posts = self.driver.find_elements(By.XPATH, "//div[@role='article']")
            if not all_posts:
                print("🤷 Không tìm thấy bài viết nào.")
                return
            random.shuffle(all_posts)
            liked_count = 0
            for post in all_posts:
                if liked_count >= num_to_like:
                    break
                try:
                    like_button = post.find_element(By.XPATH, ".//div[@aria-label='Thích']|.//div[@aria-label='Like']")
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'smooth'});", like_button)
                    time.sleep(random.uniform(0.5, 1.0))
                    self._human_click(like_button)
                    liked_count += 1
                    print(f"✅ Đã Like {liked_count}/{num_to_like}.")
                    time.sleep(random.uniform(1, 3))
                except NoSuchElementException:
                    continue
                except Exception as e:
                    print(f"⚠️ Lỗi nhỏ khi xử lý một bài viết: {e}")
                    continue
            print(f"👍 Hoàn tất! Đã Like {liked_count} bài.")
        except Exception as e:
            print(f"❌ Lỗi Like ngẫu nhiên: {e}")

    def post_to_group(self, group_url, group_name, content, image_path=None):
        try:
            print(f"\n📝 Đang xử lý nhóm: {group_name}")
            print(f"🔗 URL: {group_url}")

            self.driver.get(group_url)
            time.sleep(random.uniform(3, 5))

            # Tương tác nền
            self._like_random_posts_if_not_liked()
            self._comment_on_welcome_posts()

            if self._check_pending_approval():
                print("🟡 Bài cũ đang chờ duyệt. Bỏ qua post.")
                if MODE == "INTERACT_ONLY" or (MODE == "POST_PLUS_INTERACT" and ENABLE_BETWEEN_INTERACTIONS):
                    self.perform_human_interactions()
                return True

            if self._group_restricted():
                print("⛔ Nhóm giới hạn đăng bài. Bỏ qua.")
                if MODE == "INTERACT_ONLY":
                    self.perform_human_interactions()
                    return True
                return False

            did_post = False
            post_claimed = False

            if MODE in ("POST_ONLY", "POST_PLUS_INTERACT"):
                content_prepared = self._beautify_content(content) if ENABLE_BEAUTIFY else content
                actor = f"{platform.node()}-{os.getenv('USER') or os.getenv('USERNAME') or 'bot'}"
                claim = dedup_request("claim", group_url, actor, content_prepared)
                if not claim.get("ok"):
                    print(f"⛔ Bỏ qua group do dedup: {claim.get('error', 'đã đăng gần đây')}")
                    if MODE == "POST_PLUS_INTERACT":
                        print("🤹 Tương tác thay vì post (do dedup).")
                        self.perform_human_interactions()
                        return True
                    return False
                else:
                    post_claimed = True
                    print(f"🔒 Đã giữ chỗ đăng trong {claim.get('expires_in','?')}s.")

                if not self._open_group_composer():
                    print("❌ Không tìm thấy composer.")
                    dedup_request("release", group_url, actor, content_prepared)
                    return False

                textbox = self.wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//div[@role='dialog']//div[@role='textbox' and @contenteditable='true']")))
                self._type_multiline_js_plus_enter(textbox, content_prepared)
                time.sleep(1.0)

                if image_path and os.path.exists(image_path):
                    try:
                        file_input = None
                        for xp in [
                            "//div[@role='dialog']//input[@type='file' and not(@disabled)]",
                            "//input[@type='file' and not(@disabled)]",
                        ]:
                            els = self.driver.find_elements(By.XPATH, xp)
                            if els: file_input = els[0]; break
                        if not file_input:
                            photo_btn_xp = (
                                "//div[@role='dialog']//*[(@aria-label='Thêm ảnh/video' or @aria-label='Add Photo/Video' "
                                "or contains(normalize-space(.),'Thêm ảnh') or contains(normalize-space(.),'Add photo')) "
                                "and ancestor::div[@role='dialog']]"
                            )
                            try:
                                photo_btn = self.driver.find_element(By.XPATH, photo_btn_xp)
                                self._human_click(photo_btn) if ENABLE_HUMAN_MOUSE else self.driver.execute_script("arguments[0].click();", photo_btn)
                                time.sleep(1)
                                file_input = self.wait.until(EC.presence_of_element_located(
                                    (By.XPATH, "//div[@role='dialog']//input[@type='file' and not(@disabled)]")))
                            except Exception:
                                pass
                        if file_input:
                            file_input.send_keys(os.path.abspath(image_path))
                            print("✅ Đã upload hình ảnh"); time.sleep(3)
                        else:
                            print("⚠️  Không tìm thấy input để upload ảnh.")
                    except Exception as e:
                        print(f"⚠️  Lỗi khi upload ảnh: {e}")

                print("\n" + "="*60)
                print("🎯 BÀI ĐĂNG ĐÃ SẴN SÀNG — TỰ ĐỘNG BẤM 'Đăng'")
                print(f"📝 Group: {group_name}")
                print(f"🔗 URL: {group_url}")
                print("="*60)

                if not self._click_post_button():
                    print("❌ Không thể bấm nút Đăng.")
                    dedup_request("release", group_url, actor, content_prepared)
                    if MODE == "POST_PLUS_INTERACT":
                        self.perform_human_interactions()
                        return False
                    return False

                self._wait_post_published(timeout=25)
                print("✅ Đã gửi lệnh đăng.")
                did_post = True

                dedup_request("mark", group_url, actor, content_prepared)

            if MODE == "INTERACT_ONLY" or (MODE == "POST_PLUS_INTERACT" and ENABLE_BETWEEN_INTERACTIONS):
                if not did_post:
                    print("🤹 Bắt đầu tương tác tự nhiên...")
                    self.perform_human_interactions()

            return True if (did_post or MODE != "POST_ONLY") else False

        except Exception as e:
            print(f"❌ Lỗi khi xử lý nhóm {group_name}: {e}")
            try:
                if post_claimed and MODE in ("POST_ONLY", "POST_PLUS_INTERACT"):
                    actor = f"{platform.node()}-{os.getenv('USER') or os.getenv('USERNAME') or 'bot'}"
                    dedup_request("release", group_url, actor, "")
            except Exception:
                pass
            return False

    # ---------------- Runner tổng ----------------
    def run(self, groups_csv, content_file, image_path=None, delay_range=DEFAULT_DELAY_RANGE):
        if MODE.upper() == "JOIN_BY_LIST":
            self.run_join_by_list(TASK4JOIN_CSV)
            return
        # >>> ADD:
        if MODE.upper() == "VISIT_LIKE":
            # Nếu bạn dùng chính file CSV nhóm có header như mô tả, sửa ENV VISIT_LIKE_CSV
            self.run_visit_like(VISIT_LIKE_CSV)
            return

        print(f"🚀 Bắt đầu: MODE = {MODE}")

        groups = self.load_groups(groups_csv)
        if not groups:
            print("❌ Không có group nào để xử lý."); return

        content = ""
        if MODE in ("POST_ONLY", "POST_PLUS_INTERACT"):
            # Ưu tiên sử dụng nội dung từ posts/contents.txt
            content = self.get_random_content_from_posts()
            if not content:
                # Fallback về file cũ nếu không có
                content = self.load_post_content(content_file)
            if not content:
                print("❌ Không có nội dung để đăng."); return

        if RANDOMIZE_ORDER:
            random.shuffle(groups)
            print(f"🔀 Đã xáo trộn thứ tự {len(groups)} nhóm.")
        if isinstance(RANDOM_SAMPLE_COUNT, int) and RANDOM_SAMPLE_COUNT > 0:
            if RANDOM_SAMPLE_COUNT < len(groups):
                groups = random.sample(groups, RANDOM_SAMPLE_COUNT)
                print(f"🎯 Lấy ngẫu nhiên {len(groups)} nhóm để xử lý.")

        self.login_facebook()

        total_groups = len(groups)
        for i, group in enumerate(groups):
            print("\n" + "#"*70)
            print(f"##   NHÓM {i+1}/{total_groups}   ##")
            print("#"*70)

            # Chọn ngẫu nhiên hình ảnh từ thư mục posts
            random_image = self.get_random_image_from_posts()
            final_image_path = random_image if random_image else image_path

            self.post_to_group(
                group['url'],
                group['name'],
                content,
                final_image_path
            )

            if i < total_groups - 1:
                delay = random.uniform(*delay_range)
                print(f"\n⏳ Tạm nghỉ {delay:.1f} giây trước khi sang nhóm tiếp theo...")
                time.sleep(delay)

        print("\n" + "="*70)
        print("🎉🎉🎉 HOÀN TẤT KỊCH BẢN! 🎉🎉🎉")
        print("="*70)


# =========================
# === VISIT_LIKE PATCH ===
# =========================
try:
    VISIT_LIKE_LOG
except NameError:
    VISIT_LIKE_LOG = "visit-like.log"

def _vl_now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _vl_log(line: str):
    try:
        with open(VISIT_LIKE_LOG, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass

# -------- helpers (scoped outside class; use self.driver) --------
def _open_article_permalink_js(driver, article):
    # Return anchor element of a post/permalink inside given article (or None).
    return driver.execute_script(
        """
        const art = arguments[0];
        const as = Array.from(art.querySelectorAll('a[href]'));
        // Prefer explicit post/permalink links
        const pri = as.find(a => /\\/posts\\//.test(a.getAttribute('href')) || /\\/permalink\\//.test(a.getAttribute('href')));
        if (pri) return pri;
        // Fallback: timestamp links within article header often point to permalink
        const ts = as.find(a => (a.getAttribute('aria-hidden') === 'true') || (/\\/groups\\//.test(a.href) && /permalink/.test(a.href)));
        return ts || null;
        """,
        article
    )

def _like_post_on_permalink(driver):
    from selenium.webdriver.common.by import By
    import time, random
    # Tìm đúng thanh tác vụ của bài viết (có cả Bình luận/Comment và Chia sẻ/Share)
    try:
        block_xp = (
            "//div[.//div[@role='button' and (@aria-label='Bình luận' or @aria-label='Comment' or .//span[normalize-space(.)='Bình luận'] or .//span[normalize-space(.)='Comment'])] "
            "and .//div[@role='button' and (@aria-label='Chia sẻ' or @aria-label='Share' or .//span[normalize-space(.)='Chia sẻ'] or .//span[normalize-space(.)='Share'])] "
            "and .//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like'])]]"
        )
        blocks = driver.find_elements(By.XPATH, block_xp)
        for blk in blocks:
            btns = blk.find_elements(
                By.XPATH,
                ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like']) and not(@aria-pressed='true') and not(contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'comment') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bình luận'))]"
            )
            if btns:
                el = btns[0]
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                time.sleep(random.uniform(0.3, 0.6))
                # Hover + chọn reaction nếu có helper global
                try:
                    _react_on_button(driver, el)
                except Exception:
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                time.sleep(random.uniform(0.3, 0.6))
                return True
    except Exception:
        pass

    # Fallback: thử Like bằng phím tắt 'l'
    # fallback: use keyboard 'l' (Facebook shortcut to like)
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys("l")
        time.sleep(0.6)
        return True
    except Exception:
        return False

# Bỏ hàm _like_one_comment_on_permalink vì không cần thiết nữa

# -------- reaction helper for patched methods --------
def _react_on_button(driver, like_btn, prefer=("love","like")):
    """Hover over a like button and pick a reaction (love/like)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput
    import time, random
    try:
        ap = (like_btn.get_attribute('aria-pressed') or '').lower()
        if ap == 'true':
            return False
        try:
            rect = driver.execute_script("var r=arguments[0].getBoundingClientRect();return {x:r.left + r.width/2, y:r.top + r.height/2};", like_btn)
            if rect:
                actions = ActionBuilder(driver)
                mouse = PointerInput(PointerInput.INTERACTION_MOUSE, "mouse")
                actions.add_action(mouse)
                actions.pointer_action.move_to_location(int(rect.get('x', 0)), int(rect.get('y', 0)))
                actions.perform()
        except Exception:
            pass
        time.sleep(random.uniform(0.25, 0.55))
        pref_map = {
            'love': ["//*[@aria-label='Yêu thích']", "//*[@aria-label='Love']"],
            'like': ["//*[@aria-label='Thích']", "//*[@aria-label='Like']"],
        }
        for ch in prefer:
            for xp in pref_map.get(ch, []):
                try:
                    els = driver.find_elements(By.XPATH, xp)
                    if els:
                        el = els[0]
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        time.sleep(random.uniform(0.25, 0.5))
                        return True
                except Exception:
                    pass
        try:
            like_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", like_btn)
        time.sleep(random.uniform(0.25, 0.5))
        return True
    except Exception:
        return False

# -------- patched methods --------
def _patch_like_first_posts(self, min_count=3, max_count=5):
    from selenium.webdriver.common.by import By
    import time, random
    target = random.randint(min_count, max_count)
    liked = 0
    self.driver.execute_script("window.scrollTo({top:0,behavior:'instant'});")
    time.sleep(random.uniform(0.7, 1.1))
    arts = self.driver.find_elements(By.XPATH, "//div[@role='article']")
    if len(arts) < target:
        self.driver.execute_script("window.scrollBy({top:1000,behavior:'smooth'});")
        time.sleep(1.0)
        arts = self.driver.find_elements(By.XPATH, "//div[@role='article']")
    for art in arts:
        if liked >= target:
            break
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", art)
            time.sleep(random.uniform(0.4, 0.8))
            a = _open_article_permalink_js(self.driver, art)
            if a:
                try:
                    self.driver.execute_script("arguments[0].click();", a)
                except Exception:
                    try: a.click()
                    except Exception: pass
                time.sleep(random.uniform(1.2, 2.2))
                ok = _like_post_on_permalink(self.driver)
                _vl_log(f"[{_vl_now()}] POST-LIKE {'OK' if ok else 'MISS'} | permalink | url={self.driver.current_url}")
                if ok:
                    liked += 1
                self.driver.back()
                time.sleep(random.uniform(0.8, 1.4))
                continue
            # Fallback: Like trong bài nhưng tránh Like bình luận
            like_btn = None

            # Các XPath selectors mạnh mẽ hơn để tìm nút Like
            like_selectors = [
                # Selector chính - tìm nút Like trong thanh tác vụ bài viết
                ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like' or .//span[normalize-space(.)='Thích'] or .//span[normalize-space(.)='Like'])]",

                # Selector cho nút Like với aria-pressed
                ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like') and not(@aria-pressed='true')]",

                # Selector tìm theo span text
                ".//span[normalize-space(.)='Thích' or normalize-space(.)='Like']/ancestor::div[@role='button']",

                # Selector tìm nút Like gần nút Comment/Share
                ".//div[@role='button' and (@aria-label='Thích' or @aria-label='Like') and not(contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'comment') or contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bình luận'))]",

                # Selector cho các nút reaction (bao gồm cả nút Like đã like)
                ".//div[@role='button' and (@data-testid='fb-ufi-likelink' or @data-testid='like-button')]",

                # Selector mới cho Facebook hiện đại
                ".//div[@role='button' and contains(@class,'x1i10hfl') and (.//span[contains(text(),'Thích')] or .//span[contains(text(),'Like')])]",
            ]

            # Thử từng selector để tìm nút Like chưa được nhấn
            for selector in like_selectors:
                try:
                    candidates = art.find_elements(By.XPATH, selector)
                    # Lọc bỏ những nút đã được nhấn (aria-pressed='true')
                    available_candidates = [c for c in candidates if (c.get_attribute('aria-pressed') or '').lower() != 'true']

                    if available_candidates:
                        like_btn = available_candidates[0]
                        break
                except Exception:
                    continue

            # Nếu vẫn không tìm thấy, thử tìm bất kỳ nút Like nào và kiểm tra trạng thái sau
            if like_btn is None:
                for selector in like_selectors:
                    try:
                        candidates = art.find_elements(By.XPATH, selector)
                        if candidates:
                            like_btn = candidates[0]
                            break
                    except Exception:
                        continue
            if like_btn:
                # hover + chọn reaction (Yêu thích/Thích) để tránh miss-click
                if not _react_on_button(self.driver, like_btn, prefer=("love","like")):
                    # fallback click nếu không mở được khay reaction
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", like_btn)
                    time.sleep(random.uniform(0.2, 0.5))
                    try:
                        like_btn.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", like_btn)
                liked += 1
                _vl_log(f"[{_vl_now()}] POST-LIKE OK | inline | url={self.driver.current_url}")
                time.sleep(random.uniform(0.6, 1.0))
            else:
                _vl_log(f"[{_vl_now()}] POST-LIKE MISS | inline | url={self.driver.current_url}")
                self.driver.execute_script("window.scrollBy({top:240,behavior:'instant'});")
                time.sleep(random.uniform(0.25, 0.5))
        except Exception as e:
            _vl_log(f"[{_vl_now()}] POST-LIKE ERR | {type(e).__name__}: {e}")
    print(f"✅ VISIT_LIKE: Like bài viết {liked}/{target}.")
    _vl_log(f"[{_vl_now()}] SUMMARY POST-LIKE {liked}/{target} | url={self.driver.current_url}")

# Bỏ hàm _patch_comment_on_random_posts để code gọn hơn

# Bỏ hàm _patch_like_comments_across_posts để code gọn hơn

def _patch_run_visit_like(self, csv_path: str):
    import time, random
    print("🚀 Bắt đầu chế độ VISIT_LIKE")
    try:
        groups = self._read_groups_for_visit_like(csv_path)
    except Exception:
        groups = []
    if not groups:
        print("❌ Không có group hợp lệ để VISIT_LIKE.")
        _vl_log(f"[{_vl_now()}] NO-GROUP")
        return
    # shuffle order - trộn ngẫu nhiên thứ tự các group
    import random as _r
    _r.shuffle(groups)
    print(f"🔀 Đã trộn ngẫu nhiên thứ tự {len(groups)} nhóm.")

    self.login_facebook()
    for idx, g in enumerate(groups, 1):
        print("\n" + "#"*70)
        print(f"## VISIT_LIKE {idx}/{len(groups)}: {g['name']}")
        print("#"*70)
        try:
            self.driver.get(g["url"])
            _vl_log(f"[{_vl_now()}] OPEN | {g['url']}")
            import time as _t, random as _rd
            _t.sleep(_rd.uniform(3.0, 5.0))
            
            # Tải nội dung bài đăng mới cho mỗi nhóm
            content = ""
            if VISIT_LIKE_ENABLE_POST:
                try:
                    # Ưu tiên sử dụng nội dung từ posts/contents.txt
                    content = self.get_random_content_from_posts()
                    if not content:
                        # Fallback về file cũ nếu không có
                        content = self.load_post_content(DEFAULT_CONTENT_TXT)
                    if not content:
                        print("⚠️ Không có nội dung để đăng, chỉ thực hiện tương tác.")
                except Exception as e:
                    print(f"⚠️ Lỗi khi tải nội dung: {e}")
            
            # Đăng bài trước nếu được bật và có nội dung
            if VISIT_LIKE_ENABLE_POST and content and random.random() < VISIT_LIKE_POST_PROBABILITY:
                print(f"📝 Thực hiện đăng bài (xác suất: {VISIT_LIKE_POST_PROBABILITY})")
                _vl_log(f"[{_vl_now()}] POST-ATTEMPT | {g['url']}")
                try:
                    # Chọn ngẫu nhiên hình ảnh từ thư mục posts
                    random_image = self.get_random_image_from_posts()
                    image_path = random_image if random_image else DEFAULT_IMAGE_PATH
                    
                    # Sử dụng hàm đăng bài đơn giản
                    success = self.simple_post_to_group(content, image_path)
                    if success:
                        print("✅ Đã đăng bài thành công")
                        _vl_log(f"[{_vl_now()}] POST-SUCCESS | {g['url']}")
                    else:
                        print("⚠️ Không thể đăng bài, tiếp tục tương tác")
                        _vl_log(f"[{_vl_now()}] POST-FAIL | {g['url']}")
                except Exception as e:
                    print(f"⚠️ Lỗi khi đăng bài: {e}")
                    _vl_log(f"[{_vl_now()}] POST-ERR | {g['url']} | {e}")
            else:
                if not VISIT_LIKE_ENABLE_POST:
                    print("ℹ️ Chế độ đăng bài đã tắt")
                elif not content:
                    print("ℹ️ Không có nội dung để đăng")
                else:
                    print(f"ℹ️ Bỏ qua đăng bài (xác suất: {VISIT_LIKE_POST_PROBABILITY})")
                    _vl_log(f"[{_vl_now()}] POST-SKIP | {g['url']}")
            
            # Thực hiện tương tác Like sau khi đăng bài
            _patch_like_first_posts(self, 3, 5)
                    
        except KeyboardInterrupt:
            print("\n🛑 Dừng bởi người dùng.")
            _vl_log(f"[{_vl_now()}] STOP-BY-USER")
            break
        except Exception as e:
            print(f"⚠️ Lỗi tại group này: {e}")
            _vl_log(f"[{_vl_now()}] GROUP-ERR | {g['url']} | {e}")
        finally:
            now = _vl_now()
            try:
                self._write_last_updated(csv_path, g.get('row_index', -1), now)
            except Exception as _e:
                pass
            _vl_log(f"[{_vl_now()}] LASTUPDATED | {g['url']} | {now}")
        delay = __import__('random').uniform(*DEFAULT_DELAY_RANGE)
        print(f"⏳ Nghỉ {delay:.1f}s trước nhóm tiếp theo…")
        __import__('time').sleep(delay)
    print("\n🎉 Hoàn tất VISIT_LIKE")
    _vl_log(f"[{_vl_now()}] DONE")

# ---- bind to class ----
        
if __name__ == "__main__":
    poster = UniversalFacebookPoster()
    try:
        poster.run(
            groups_csv=DEFAULT_GROUPS_CSV,
            content_file=DEFAULT_CONTENT_TXT,
            image_path=DEFAULT_IMAGE_PATH,
            delay_range=DEFAULT_DELAY_RANGE
        )
    except KeyboardInterrupt:
        print("\n🛑 Người dùng đã dừng chương trình.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n💥 LỖI KHÔNG XÁC ĐỊNH: {e}")
    finally:
        poster.close()




try:
    UniversalFacebookPoster.like_first_posts = _patch_like_first_posts
    UniversalFacebookPoster.run_visit_like = _patch_run_visit_like
    _vl_log(f"[{_vl_now()}] PATCH APPLIED")
except Exception as _e:
    _vl_log(f"[{_vl_now()}] PATCH BIND ERROR | {_e}")
