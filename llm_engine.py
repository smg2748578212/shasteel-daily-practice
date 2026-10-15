import os
import re
from openai import OpenAI
import config
from exceptions import LLMError


def _load_api_key() -> str:
    """从文件或环境变量加载 DeepSeek API Key。"""
    env_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if env_key:
        return env_key

    token_file = config.DEEPSEEK_TOKEN_FILE
    if os.path.exists(token_file):
        return open(token_file).read().strip()

    raise LLMError("未找到 DeepSeek API Key，请设置 DEEPSEEK_API_KEY 环境变量或 "
                   f"确保文件 {token_file} 存在")


def _build_prompt(question: str, options: list) -> str:
    """构建 LLM 答题提示词。"""
    options_text = "\n".join(options) if options else "（无法识别选项）"
    return f"""你是一个答题助手。请根据以下题目和选项，选出最正确的答案。
只回复答案的字母（如 A、B、C、D），然后附带简短解释。

题目：{question}

选项：
{options_text}

答案："""


def _parse_answer(response_text: str, options: list) -> dict:
    """解析 LLM 回复，提取答案字母和解释。"""
    text = response_text.strip()

    # 尝试匹配 A/B/C/D 或 A、B、C、D
    match = re.search(r"^\s*([A-Da-d])[.\s、:：]?", text)
    if match:
        answer = match.group(1).upper()
        explanation = text[match.end():].strip()
        return {"answer": answer, "explanation": explanation}

    # 尝试在文本中查找第一个选项字母
    match = re.search(r"[A-Da-d]", text)
    if match:
        answer = match.group().upper()
        return {"answer": answer, "explanation": text}

    # 无法解析，返回第一个选项作为默认
    if options:
        return {"answer": "A", "explanation": f"无法解析 LLM 回复，默认选 A: {text[:100]}"}

    raise LLMError(f"无法从 LLM 回复中提取答案: {text[:200]}")


def _call_deepseek(question: str, options: list, model: str) -> dict:
    """调用 DeepSeek API 获取答案。"""
    if not model:
        raise LLMError("请设置 DEEPSEEK_MODEL_FAST 和 DEEPSEEK_MODEL_PRO")
    api_key = _load_api_key()
    client = OpenAI(api_key=api_key, base_url=config.DEEPSEEK_BASE_URL)

    prompt = _build_prompt(question, options)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
    except Exception as e:
        raise LLMError(f"DeepSeek API 调用失败: {e}")

    content = response.choices[0].message.content
    result = _parse_answer(content, options)
    result["model_used"] = model
    result["raw_response"] = content
    result["usage"] = {
        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
    }
    return result


def answer_question(question: str, options: list) -> dict:
    """
    使用 DeepSeek LLM 推理答案。
    先用 fast 模型，若答案可信度低则回退 pro 模型。

    返回 {"answer": "A", "explanation": "...", "model_used": "...", ...}
    """
    # 先用 fast 模型
    try:
        result = _call_deepseek(question, options, config.DEEPSEEK_MODEL_FAST)
    except LLMError:
        # fast 失败，尝试 pro
        result = _call_deepseek(question, options, config.DEEPSEEK_MODEL_PRO)

    # 验证答案是否在有效选项中
    valid_letters = _option_letters(options)
    if result["answer"] not in valid_letters and valid_letters:
        # 答案无效，用 pro 重试
        try:
            result = _call_deepseek(question, options, config.DEEPSEEK_MODEL_PRO)
            if result["answer"] not in valid_letters:
                result["answer"] = valid_letters[0]
                result["explanation"] += " (答案无效，默认选第一个)"
        except LLMError:
            result["answer"] = valid_letters[0]

    return result


def _option_letters(options: list) -> list:
    """提取选项的字母标识列表，如 ['A', 'B', 'C', 'D']。"""
    letters = []
    for opt in options:
        match = re.match(r"([A-D])", opt.strip())
        if match:
            letters.append(match.group(1))
    return letters
