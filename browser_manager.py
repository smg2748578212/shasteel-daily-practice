import os
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext
import config


class BrowserManager:
    """Playwright 浏览器生命周期管理，支持持久化 session（减少 API 登录）。"""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            executable_path=config.BROWSER_EXECUTABLE or None,
            headless=config.HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ] + (["--headless=new"] if config.HEADLESS else []),
        )
        # 加载持久化 session（cookies + localStorage）
        storage_state = None
        if config.BROWSER_STATE_FILE.exists():
            try:
                storage_state = str(config.BROWSER_STATE_FILE)
            except Exception:
                pass

        self._context = self._browser.new_context(
            viewport={"width": config.VIEWPORT_WIDTH, "height": config.VIEWPORT_HEIGHT},
            device_scale_factor=config.DEVICE_SCALE_FACTOR,
            locale="zh-CN",
            storage_state=storage_state,
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(config.BROWSER_TIMEOUT)
        return self, self._page

    def save_state(self):
        """保存浏览器状态到文件，供下次复用。"""
        if self._context:
            try:
                self._context.storage_state(path=str(config.BROWSER_STATE_FILE))
            except Exception:
                pass

    def __exit__(self, *args):
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
