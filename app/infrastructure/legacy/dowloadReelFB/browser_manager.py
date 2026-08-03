"""Removed browser owner.

Facebook pages are now exclusively controlled by `FacebookBrowserWorker`.
"""


class BrowserManager:
    def start(self) -> bool:
        raise RuntimeError(
            "Direct browser access was removed; submit a Facebook job and run "
            "scripts/run_facebook_worker.sh"
        )

    def create_page(self):
        return self.start()

    def stop(self) -> None:
        return None
