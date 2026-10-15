import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import config as _config

# 北京时间
TZ_BEIJING = timezone(timedelta(hours=8))


class Logger:
    def __init__(self):
        self.start_time = time.time()
        self.date_str = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")
        self.session_dir = _config.SCREENSHOT_DIR / self.date_str
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.config_log_dir = _config.LOG_DIR
        self.config_log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d_%H%M%S")
        self.log_file = self.config_log_dir / f"run_{timestamp}.json"
        self._entries = []
        self._current_stage = ""
        self._screenshot_counter = 0

    def _now(self) -> str:
        return datetime.now(TZ_BEIJING).isoformat()

    def _entry(self, level: str, message: str, **extra):
        record = {
            "timestamp": self._now(),
            "level": level,
            "stage": self._current_stage,
            "message": message,
            **extra,
        }
        self._entries.append(record)
        self._write_line(record)

    def _write_line(self, record: dict):
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stage(self, name: str):
        self._current_stage = name
        self.info(f"进入阶段: {name}")

    def info(self, message: str, **extra):
        self._entry("INFO", message, **extra)

    def warn(self, message: str, **extra):
        self._entry("WARN", message, **extra)

    def error(self, message: str, **extra):
        self._entry("ERROR", message, **extra)

    def save_screenshot(self, page_or_bytes, name: str):
        self._screenshot_counter += 1
        idx = str(self._screenshot_counter).zfill(2)
        path = self.session_dir / f"{idx}_{name}.png"

        if hasattr(page_or_bytes, "screenshot"):
            page_or_bytes.screenshot(path=str(path), full_page=False)
        else:
            path.write_bytes(page_or_bytes)

        self.info(f"截图已保存: {path.name}")
        return path

    def log_question(self, index: int, question_text: str, options_text: list,
                     answer: str, explanation: str, model_used: str):
        self.info(
            f"第 {index} 题",
            question=question_text,
            options=options_text,
            answer=answer,
            explanation=explanation,
            model=model_used,
        )

    def summary(self, success: bool, total_questions: int, detail: str = ""):
        elapsed = time.time() - self.start_time
        self.info(
            "执行完成",
            success=success,
            total_questions=total_questions,
            elapsed_seconds=round(elapsed, 1),
            detail=detail,
        )

    def flush(self):
        """确保日志落盘。"""
        pass
