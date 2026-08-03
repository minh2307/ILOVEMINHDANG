import logging
import traceback
import sys
from config import LOG_FILE

def setup_logger(name: str = "fb_downloader") -> logging.Logger:
    """Cấu hình logging thống nhất ghi cả ra Console và File log."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    # File Handler
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console Handler (Rich Console được dùng làm giao diện, ở đây ta ghi log cơ bản ra stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

def log_exception(logger_obj: logging.Logger, message: str, exc: Exception) -> None:
    """Ghi log exception chi tiết bao gồm file, dòng code và tên hàm gây lỗi."""
    exc_type, exc_value, exc_traceback = sys.exc_info()
    tb_details = traceback.extract_tb(exc_traceback)
    if tb_details:
        last_tb = tb_details[-1]
        file_name = last_tb.filename
        line_num = last_tb.lineno
        func_name = last_tb.name
        logger_obj.error(f"{message}: {exc} (File: {file_name}, Line: {line_num}, Func: {func_name})")
    else:
        logger_obj.error(f"{message}: {exc}")
    
    # Ghi log chi tiết traceback
    logger_obj.debug(traceback.format_exc())
