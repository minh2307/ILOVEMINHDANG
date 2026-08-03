import re
from urllib.parse import urlparse

def is_facebook_reel(url: str) -> bool:
    """
    Kiểm tra xem một URL có phải là Facebook Reel hay không.
    
    Các dạng URL hỗ trợ:
    - https://www.facebook.com/reel/...
    - https://fb.watch/...
    - https://www.facebook.com/share/r/...
    - https://m.facebook.com/reel/...
    
    Args:
        url (str): URL cần kiểm tra.
        
    Returns:
        bool: True nếu là Reel, False nếu không phải.
    """
    if not url:
        return False
    
    url = url.strip()
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        path = parsed.path
        
        # 1. Dạng fb.watch
        if "fb.watch" in netloc:
            return True
            
        # 2. Dạng facebook.com (www., m., mobile., web., v.v.)
        if "facebook.com" in netloc:
            # /reel/... hoặc /share/r/...
            if path.startswith("/reel") or path.startswith("/share/r"):
                return True
                
        # Regex bổ trợ tổng quát hơn phòng trường hợp urlparse không bắt hết hoặc URL có dạng đặc biệt
        # Bắt: facebook.com/reel/..., facebook.com/share/r/..., fb.watch/...
        pattern = r'https?://(?:[a-zA-Z0-9\-]+\.)?(?:facebook\.com/(?:reel|share/r)|fb\.watch)(?:/|\?|$)'
        if re.search(pattern, url, re.IGNORECASE):
            return True
            
    except Exception:
        return False
        
    return False
