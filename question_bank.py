import os
import re
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl


BANK_PATH = Path(os.environ.get("QUESTION_BANK_PATH", str(Path(__file__).resolve().parent / "data" / "question_bank.xlsx")))
MATCH_THRESHOLD = 0.55  # 相似度阈值，低于此值视为未匹配


def _normalize(text: str) -> str:
    """标准化文本用于比较：去空格、统一标点。"""
    if not text:
        return ""
    text = text.strip()
    # 统一空白
    text = re.sub(r"\s+", "", text)
    # 统一括号
    text = text.replace("（）", "()").replace("（", "(").replace("）", ")")
    text = text.replace("：", ":").replace("，", ",").replace("。", ".")
    text = text.replace("；", ";").replace("、", ",").replace("＂", "\"")
    text = text.replace(""", "\"").replace(""", "\"")
    return text


def _similarity(a: str, b: str) -> float:
    """计算两个文本的相似度 (0.0 ~ 1.0)。"""
    return SequenceMatcher(None, a, b).ratio()


class QuestionBank:
    """应知应会题库，支持模糊匹配查找答案。"""

    def __init__(self, path: str | Path = None):
        if path is None:
            path = BANK_PATH
        self._path = Path(path)
        self._single_choice: list[dict] = []  # [{question, options, answer, normalized}]
        self._true_false: list[dict] = []     # [{question, answer, normalized}]
        self._load()

    def _load(self):
        """加载 Excel 题库。"""
        wb = openpyxl.load_workbook(self._path, read_only=True)

        # 单选题
        ws1 = wb["单选题样题"]
        for row in ws1.iter_rows(min_row=2, values_only=True):
            if not row[2]:
                continue
            q = str(row[2]).strip()
            if not q:
                continue
            self._single_choice.append({
                "question": q,
                "options": [str(row[i]) if row[i] else "" for i in range(3, 7)],
                "answer": str(row[7]).strip() if row[7] else "",
                "normalized": _normalize(q),
            })

        # 判断题
        ws2 = wb["判断题样题"]
        for row in ws2.iter_rows(min_row=2, values_only=True):
            if not row[2]:
                continue
            q = str(row[2]).strip()
            if not q:
                continue
            raw_answer = str(row[3]).strip() if row[3] else ""
            # 1 → 对(A), 2 → 错(B)
            answer_map = {"1": "A", "2": "B"}
            self._true_false.append({
                "question": q,
                "answer": answer_map.get(raw_answer, raw_answer),
                "normalized": _normalize(q),
            })

        wb.close()

    @property
    def single_choice_count(self) -> int:
        return len(self._single_choice)

    @property
    def true_false_count(self) -> int:
        return len(self._true_false)

    def find_answer(self, question_text: str, options: list = None) -> dict | None:
        """
        在题库中查找匹配的题目并返回答案。

        Args:
            question_text: 从页面提取的题目文本
            options: 页面显示的选项列表 (可选，用于判断题自动映射)

        Returns:
            {"answer": "A", "source": "bank_single"|"bank_tf", "question": "...",
             "score": 0.92} 或 None
        """
        q_normalized = _normalize(question_text)
        if not q_normalized:
            return None

        best_result = None
        best_score = 0

        # 1. 搜索单选题库
        for item in self._single_choice:
            score = _similarity(q_normalized, item["normalized"])
            if score > best_score:
                best_score = score
                best_result = {
                    "answer": item["answer"],
                    "source": "bank_single",
                    "question": item["question"],
                    "score": score,
                }

        # 2. 搜索判断题库
        for item in self._true_false:
            score = _similarity(q_normalized, item["normalized"])
            if score > best_score:
                best_score = score
                best_result = {
                    "answer": item["answer"],
                    "source": "bank_tf",
                    "question": item["question"],
                    "score": score,
                }

        if best_result and best_score >= MATCH_THRESHOLD:
            return best_result

        return None


# 全局单例
_bank: QuestionBank | None = None


def get_bank() -> QuestionBank:
    global _bank
    if _bank is None:
        _bank = QuestionBank()
    return _bank


def find_answer(question_text: str, options: list = None) -> dict | None:
    """便捷函数：在题库中查找题目答案。"""
    return get_bank().find_answer(question_text, options)


def _option_value(opt: str) -> str:
    """提取选项的值（去掉前缀 A./B: 等）。"""
    stripped = opt.strip()
    # 去掉 A. A: A、 A： 等前缀
    stripped = re.sub(r"^[A-D][.、:：\s]+", "", stripped)
    return stripped


def _is_tf_question(options: list) -> bool:
    """判断是否为判断题：选项去掉前缀后值为"对"或"错"，忽略 None 值。"""
    if not options:
        return False
    values = [_option_value(o) for o in options if o is not None]
    if not values:
        return False
    return all(v in ("对", "错") for v in values)


def append_question(question: str, options: list, answer: str, qtype: str = ""):
    """将新题目追加到 Excel 题库（自动去重）。"""
    bank = get_bank()
    normalized = _normalize(question)

    for item in bank._single_choice + bank._true_false:
        if _similarity(normalized, item["normalized"]) > 0.85:
            return

    wb = openpyxl.load_workbook(bank._path)

    # 判断题：qtype 明确或是选项去掉前缀后值全为"对/错"
    is_tf = qtype == "判断题" or _is_tf_question(options)

    if is_tf:
        ws = wb["判断题样题"]
        # answer 可能是 "对"/"错" 或 "A"/"B"，统一转换为 "1"/"2"
        ans_map = {"对": "1", "错": "2", "A": "1", "B": "2"}
        tf_answer = ans_map.get(answer, answer)
        ws.append([None, None, question, tf_answer, None, None, None, None])
    else:
        ws = wb["单选题样题"]
        padded = list(options) + [""] * (4 - len(options))
        ws.append([None, None, question] + padded + [answer, None, None, None])

    wb.save(bank._path)
    wb.close()
    bank._load()
    print(f"[题库] 新增{'判断' if is_tf else '单选'}题: {question[:50]}...")
