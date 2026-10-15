from PIL import Image, ImageEnhance
import pytesseract
import io
import config
from exceptions import OCRError


def preprocess_image(img: Image.Image) -> Image.Image:
    """图像预处理：灰度 → 对比度增强 → 二值化"""
    img = img.convert("L")
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)
    img = img.point(lambda x: 0 if x < config.PREPROCESS_THRESHOLD else 255)
    return img


def capture_question_screenshot(page) -> bytes:
    """截取页面截图，返回 PNG 字节数据。"""
    # 尝试截取题目容器，如果找不到则截取整页
    try:
        container = page.locator(".question, .question-container, [class*=question]").first
        if container.is_visible():
            return container.screenshot()
    except Exception:
        pass
    return page.screenshot(full_page=False)


def image_to_text(img_bytes: bytes, lang: str = None) -> str:
    """对截图执行 OCR，返回文本。"""
    if lang is None:
        lang = config.OCR_LANG

    img = Image.open(io.BytesIO(img_bytes))
    processed = preprocess_image(img)

    text = pytesseract.image_to_string(
        processed,
        lang=lang,
        config=f"--psm {config.OCR_PSM}",
    )
    return text.strip()


def parse_question_options(raw_text: str) -> dict:
    """
    从 OCR 文本中分离题目和选项。
    返回 {"question": "...", "options": ["A. ...", "B. ...", ...]}
    """
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    if not lines:
        raise OCRError("OCR 结果为空")

    option_keywords = ["A.", "B.", "C.", "D.", "A、", "B、", "C、", "D、",
                       "A ", "B ", "C ", "D "]
    question_lines = []
    option_lines = []

    for line in lines:
        is_option = any(line.startswith(kw) for kw in option_keywords)
        if is_option:
            option_lines.append(line)
        else:
            question_lines.append(line)

    question = " ".join(question_lines)
    if not question:
        question = lines[0] if lines else ""

    return {"question": question, "options": option_lines}


def ocr_question(page, logger=None) -> dict:
    """
    完整 OCR 流程：截图 → 预处理 → OCR → 解析。
    返回 {"question": str, "options": [str, ...]}
    重试 3 次，每次尝试不同预处理参数。
    """
    img_bytes = capture_question_screenshot(page)
    if logger:
        logger.save_screenshot(img_bytes, "question_raw")

    last_error = None
    for attempt in range(config.MAX_RETRIES):
        try:
            # 每次尝试不同的阈值
            threshold = config.PREPROCESS_THRESHOLD - (attempt * 20)
            lang = config.OCR_LANG

            img = Image.open(io.BytesIO(img_bytes))
            processed = preprocess_image(img)
            if attempt > 0:
                processed = processed.point(lambda x: 0 if x < threshold else 255)

            text = pytesseract.image_to_string(
                processed, lang=lang, config=f"--psm {config.OCR_PSM}"
            ).strip()

            if not text:
                continue

            result = parse_question_options(text)
            if result["options"]:
                return result

        except OCRError:
            last_error = OCRError(f"OCR 第 {attempt + 1} 次解析失败")
        except Exception as e:
            last_error = OCRError(f"OCR 异常: {e}")

    raise last_error or OCRError("OCR 全部重试失败")
