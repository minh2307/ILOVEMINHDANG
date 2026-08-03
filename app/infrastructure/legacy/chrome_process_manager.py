"""Unreferenced alternate Chrome launcher retained as legacy source only."""

import subprocess
import requests

class ChromeProcessManager:
    def __init__(self, cdp_port: int, profile_dir: str):
        self._cdp_port = cdp_port
        self._profile_dir = profile_dir
        self._process = None

    def start_chrome(self) -> None:
        if self.is_running():
            return
        cmd = [
            "google-chrome",
            f"--remote-debugging-port={self._cdp_port}",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        self._process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def is_running(self) -> bool:
        try:
            response = requests.get(f"http://127.0.0.1:{self._cdp_port}/json/version", timeout=1)
            return response.status_code == 200
        except Exception:
            return False

    def stop_chrome(self) -> None:
        if self._process:
            self._process.terminate()
            self._process = None
