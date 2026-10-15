class DailyPracticeError(Exception):
    """每日一练自动化基础异常。"""

class LoginError(DailyPracticeError):
    """登录失败（密码错误、账号不存在等）。"""

class TokenExpiredError(DailyPracticeError):
    """会话 token 已过期。"""

class NoQuestionsTodayError(DailyPracticeError):
    """今日无题目（节假日或已完成）。"""

class AlreadyCompletedError(DailyPracticeError):
    """今日每日一练已完成。"""

class OCRError(DailyPracticeError):
    """OCR 识别失败，无法提取文字。"""

class LLMError(DailyPracticeError):
    """DeepSeek API 调用失败。"""

class BrowserError(DailyPracticeError):
    """Playwright/Chromium 异常。"""

class NetworkError(DailyPracticeError):
    """网络不可达。"""

class SubmissionError(DailyPracticeError):
    """提交失败或提交结果尚未确认，不能自动重复提交。"""
