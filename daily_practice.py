import re
from playwright.sync_api import Page
import config
from llm_engine import answer_question as llm_answer
from question_bank import find_answer as bank_find_answer
from exceptions import (
    NoQuestionsTodayError,
    AlreadyCompletedError,
    BrowserError,
)


class DailyPracticeRunner:
    """每日一练 SPA 答题引擎。"""

    def __init__(self, page: Page, logger):
        self.page = page
        self.logger = logger
        self.question_count = 0
        self.total_questions = 0
        # 统计
        self.retry_count = 0
        self.wrong_count = 0
        self.bank_hits = 0
        self.llm_hits = 0
        self.answers_log = []  # [{question, options, answer, source, qtype}, ...]
        self.answer_key = {}   # {idx: correct_letter} from analysis page
        self._first_pass_answers = {}  # {idx: answer_letter}

    @property
    def new_bank_items(self) -> list:
        """返回可入库的 LLM 答对题目（经正确答案验证）。"""
        items = []
        for entry in self.answers_log:
            if entry["source"] != "llm":
                continue
            if self.answer_key:
                correct = self.answer_key.get(entry["idx"])
                if correct and entry["answer"].upper() == correct.upper():
                    entry = dict(entry, answer=correct)
                else:
                    continue
            else:
                continue

            # 判断题入库时转换为"对/错"，判断依据是选项含"对"或"错"
            options = entry.get("options", [])
            is_tf = bool(options) and any(
                "对" in o or "错" in o for o in options
            )
            if is_tf:
                entry = dict(entry, answer="对" if entry["answer"].upper() == "A" else "错")
            items.append(entry)
        return items

    def run(self, practice_url: str = None, retry_on_fail: bool = True):
        if practice_url:
            self._practice_url = practice_url
            self.logger.info("导航到每日一练（登录链接已隐藏）")
            self.page.goto(practice_url, wait_until="domcontentloaded",
                          timeout=config.BROWSER_LONG_TIMEOUT)
            self._wait_for_app_ready()
        else:
            # 持久化 session 复用，从当前页面 URL 获取
            self._practice_url = self.page.url
            self.logger.info("使用当前页面（已持久化登录）")
        self._check_page_state()
        self._click_start_if_needed()
        self._answer_loop()
        self._confirm_submission()

        # 检查结果，如果失败则重试
        body = self._get_page_text()
        if "答错" in body and "重新作答" in body and retry_on_fail:
            self.retry_count = 1
            # 提取答错题数
            m = re.search(r"答错\s*(\d+)\s*道", body)
            if m:
                self.wrong_count = int(m.group(1))
            self.logger.info(f"未通过（答错 {self.wrong_count} 题），获取正确答案后重试...")
            self.answer_key = self._collect_answer_key()
            self.logger.info(f"获取到 {len(self.answer_key)} 道题的正确答案")
            self._click_restart()
            self._retry_with_answers(self.answer_key)
            self._confirm_submission()

        self._verify_completion()

    # ── 页面加载 ───────────────────────────────────────────

    def _wait_for_app_ready(self, timeout: int = None):
        if timeout is None:
            timeout = config.BROWSER_LONG_TIMEOUT
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            self.logger.warn("networkidle 超时，尝试继续...")
            self.page.wait_for_timeout(5000)
        self.logger.save_screenshot(self.page, "app_loaded")

    def _check_page_state(self):
        content = self.page.content()
        # 已经完成并达标
        if any(kw in content for kw in ["已完成", "已答题", "提交成功"]):
            raise AlreadyCompletedError("今日每日一练已完成")
        if any(kw in content for kw in ["暂无题目", "没有题目", "敬请期待", "暂无数据"]):
            raise NoQuestionsTodayError("今日无题目")
        # 结果页面（通过/未通过）
        body = self._get_page_text()
        if "查看解析" in body or "重新作答" in body:
            # 检查是否达标
            if any(kw in body for kw in ["答错", "答对"]):
                # 已提交过，检查结果
                if "答错0道" in body or "答错 0 道" in body:
                    raise AlreadyCompletedError("今日每日一练已通过（全对）")
                # 未通过，但已经提交过了
                if "重新作答" in body:
                    self.logger.info(f"检测到结果页面: {body[:100]}")
                    self.logger.info("上次提交未达标，可以重新作答")

    def _click_start_if_needed(self):
        try:
            btn = self.page.get_by_text("开始答题")
            if btn.is_visible(timeout=2000):
                btn.click()
                self.logger.info("点击「开始答题」")
                self.page.wait_for_timeout(2000)
                return
        except Exception:
            pass
        for text in ["开始练习", "进入答题", "开始"]:
            try:
                btn = self.page.get_by_text(text, exact=True)
                if btn.is_visible(timeout=1000):
                    btn.click()
                    self.logger.info(f"点击「{text}」")
                    self.page.wait_for_timeout(2000)
                    return
            except Exception:
                continue

    # ── DOM 文本提取 ───────────────────────────────────────

    def _get_page_text(self) -> str:
        """获取页面可见文本。"""
        try:
            return self.page.locator("body").inner_text()
        except Exception:
            return ""

    def _parse_question(self, page_text: str) -> dict | None:
        """
        从 inner_text() 输出解析题目和选项。
        页面格式:
            每日一练
            作答 · 第 X 题 / 共N 题
            1 2 3 4 ...
            单选题 / 多选题 / 判断题
            题目文本
            A.选项1
            B.选项2
            ...
            上一题
            下一题

        返回 {"question": "...", "options": ["A.对", "B.错"], "qtype": "单选题",
               "qindex": 1, "total": 10} 或 None
        """
        lines = [l.strip() for l in page_text.split("\n") if l.strip()]
        if not lines:
            return None

        # 跳过 "每日一练" 标题
        start = 0
        if lines[0] == "每日一练":
            start = 1

        # 解析题号: "作答 · 第 X 题 / 共N 题"
        qindex, total = 0, 0
        for i, line in enumerate(lines[start:], start):
            m = re.search(r"第\s*(\d+)\s*题\s*/\s*共\s*(\d+)\s*题", line)
            if m:
                qindex = int(m.group(1))
                total = int(m.group(2))
                start = i + 1
                break

        if total > 0:
            self.total_questions = total

        # 跳过进度数字行 (1 2 3 4 5 ...)
        for i, line in enumerate(lines[start:], start):
            if re.match(r"^[\d\s]+$", line) and len(line.split()) >= 3:
                start = i + 1
                break

        # 题目类型
        qtype = ""
        for i, line in enumerate(lines[start:], start):
            if line in ("单选题", "多选题", "判断题"):
                qtype = line
                start = i + 1
                break

        # 题目文本
        question = ""
        for i, line in enumerate(lines[start:], start):
            # 如果已经到了选项行，停止
            if re.match(r"^[A-D][.、:：\s]", line):
                start = i
                break
            question += line
            start = i + 1

        # 用"（）"或"()"判断题目结束
        if "（）" in question or "()" in question:
            question = question.strip()
        elif not question.strip():
            question = lines[start - 1] if start > 0 else ""

        # 题目和选项在同一行的情况（如"题目。 A.选项 B.选项 C.选项 D.选项"）
        # 在 question 尾部找"。"或"）"后如果还有 A. B. C. D. 模式，说明选项也在同一行
        for sep in ["。", "）"]:
            if sep in question:
                parts = question.rsplit(sep, 1)
                candidate = parts[1].strip()
                m = re.search(r"[A-D][.、:：\s]", candidate)
                if m:
                    question = parts[0].strip()
                    rest_of_line = candidate[m.start():].strip()
                    opt_matches = re.split(r"(?=[A-D][.、:：\s])", rest_of_line)
                    options = []
                    for o in opt_matches:
                        o = o.strip()
                        if not o:
                            continue
                        # 去掉末尾的控制按钮文字
                        for btn in ("上一题", "下一题", "提交", "交卷", "确认提交"):
                            if btn in o:
                                o = o.replace(btn, "").strip()
                        if o and re.match(r"^[A-D]", o):
                            options.append(o)
                    if options:
                        return {
                            "question": question.strip(),
                            "options": options,
                            "qtype": qtype,
                            "qindex": qindex,
                            "total": total,
                        }

        # 选项
        options = []
        for i, line in enumerate(lines[start:], start):
            if re.match(r"^[A-D][.、:：\s]", line):
                options.append(line)
            elif line in ("上一题", "下一题", "提交", "交卷", "确认提交"):
                break

        if not question or not options:
            return None

        return {
            "question": question.strip(),
            "options": options,
            "qtype": qtype,
            "qindex": qindex,
            "total": total,
        }

    # ── 答题循环 ───────────────────────────────────────────

    def _answer_loop(self):
        max_questions = self.total_questions or 50
        prev_text = ""
        submitted = False

        for i in range(max_questions):
            self.question_count += 1
            idx = self.question_count

            self.page.wait_for_timeout(1500)
            self._check_page_state()

            page_text = self.page.content()
            if any(kw in page_text for kw in ["提交成功", "已完成", "答题完成",
                                               "查看解析", "重新作答",
                                               "/answer/result"]):
                self.logger.info("检测到完成状态，退出答题循环")
                break

            if page_text == prev_text and i > 0:
                self.logger.warn("页面内容无变化，可能已无更多题目")
                break
            prev_text = page_text

            self.logger.info(f"正在处理第 {idx} 题...")

            # 1. DOM 文本提取
            body_text = self._get_page_text()
            q_data = self._parse_question(body_text)

            if not q_data:
                self.logger.error(f"第 {idx} 题 DOM 解析失败，尝试 OCR...")
                self.logger.save_screenshot(self.page, f"dom_parse_failed_{idx}")
                q_data = self._ocr_fallback(idx)

            if not q_data:
                self.logger.error(f"第 {idx} 题 OCR 也失败，跳过")
                continue

            question = q_data["question"]
            options = q_data["options"]

            self.logger.info(f"题目: {question[:100]}")
            self.logger.info(f"选项: {options}")

            # 2. LLM 推理
            result = self._get_answer(question, options, idx)

            # 3. 点击选项
            answer_letter = result["answer"]
            self._click_option(answer_letter, options)

            # 4. 下一题 或 提交
            self.page.wait_for_timeout(800)
            is_last = (idx >= self.total_questions) if self.total_questions else False
            if is_last:
                submitted = self._submit_and_wait()
                if submitted:
                    break
            else:
                self._click_next_or_submit(is_last=False)

        self.logger.info(f"答题循环结束，共处理 {self.question_count} 题")

    def _ocr_fallback(self, idx: int) -> dict | None:
        """OCR 兜底：截图 → OCR → 解析。"""
        try:
            from ocr_engine import ocr_question
            return ocr_question(self.page, self.logger)
        except Exception as e:
            self.logger.error(f"OCR 兜底也失败: {e}")
            return None

    def _get_answer(self, question: str, options: list, idx: int) -> dict:
        """获取答案：题库优先匹配，未命中则用 LLM。"""
        # 1. 题库匹配
        bank_result = bank_find_answer(question, options)
        if bank_result:
            self.bank_hits += 1
            self.logger.info(
                f"题库命中 [{bank_result['source']}] "
                f"答案={bank_result['answer']} 相似度={bank_result['score']:.2f}"
            )
            result = {
                "answer": bank_result["answer"],
                "explanation": f"题库匹配 [{bank_result['source']}] "
                               f"score={bank_result['score']:.2f}",
                "model_used": "question_bank",
            }
            self.logger.log_question(
                idx, question, options,
                result["answer"], result["explanation"], result["model_used"],
            )
            self._first_pass_answers[idx] = result["answer"]
            self.answers_log.append({
                "idx": idx, "question": question, "options": options,
                "answer": result["answer"], "source": "bank",
                "qtype": bank_result.get("source", ""),
            })
            return result

        # 2. LLM 兜底
        self.llm_hits += 1
        try:
            result = llm_answer(question, options)
        except Exception as e:
            self.logger.error(f"LLM 失败: {e}")
            result = {"answer": "A", "explanation": f"LLM 异常: {e}",
                       "model_used": "fallback"}

        self.logger.log_question(
            idx, question, options,
            result["answer"],
            result.get("explanation", ""),
            result.get("model_used", "unknown"),
        )
        self._first_pass_answers[idx] = result["answer"]
        self.answers_log.append({
            "idx": idx, "question": question, "options": options,
            "answer": result["answer"], "source": "llm",
            "qtype": result.get("model_used", ""),
        })
        return result

    # ── 页面交互 ───────────────────────────────────────────

    def _click_option(self, answer_letter: str, options: list):
        """点击指定字母的选项。"""
        option_index = ord(answer_letter.upper()) - ord("A")
        self.logger.info(f"点击选项 {answer_letter} (索引 {option_index})")

        # 策略 1: 使用已知的选项 CSS class
        try:
            sel = "[class*=_optionText_]"
            items = self.page.locator(sel)
            count = items.count()
            if count > option_index:
                items.nth(option_index).click(timeout=5000)
                self.logger.info(f"通过 CSS class 点击成功")
                return
        except Exception:
            pass

        # 策略 2: 文本匹配
        for opt in options:
            stripped = opt.strip()
            if stripped.startswith(answer_letter + ".") or \
               stripped.startswith(answer_letter + "：") or \
               stripped.startswith(answer_letter + ":"):
                try:
                    self.page.get_by_text(stripped).click(timeout=5000)
                    self.logger.info(f"通过文本匹配点击成功: {stripped}")
                    return
                except Exception:
                    continue

        # 策略 3: 按选项索引用通用选择器点击
        selectors = ["[class*=option]", "[class*=choice]", "[class*=answer]"]
        for sel in selectors:
            try:
                items = self.page.locator(sel)
                if items.count() > option_index:
                    items.nth(option_index).click(timeout=3000)
                    self.logger.info(f"通过选择器 {sel} 点击成功")
                    return
            except Exception:
                continue

        self.logger.warn(f"无法点击选项 {answer_letter}")
        self.logger.save_screenshot(self.page, f"click_failed_{answer_letter}")

    def _click_next_or_submit(self, is_last: bool = False):
        """点击「下一题」或「提交」。"""
        if is_last:
            submit_texts = ["提交", "交卷", "提交答案", "完成"]
            for text in submit_texts:
                try:
                    btn = self.page.get_by_text(text)
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        self.logger.info(f"点击「{text}」")
                        return
                except Exception:
                    continue
            self.logger.warn("最后一题未找到提交按钮！")
            self.logger.save_screenshot(self.page, "no_submit_button_last")
            return

        # 优先找「下一题」
        try:
            btn = self.page.get_by_text("下一题")
            if btn.is_visible(timeout=2000):
                btn.click()
                self.logger.info("点击「下一题」")
                return
        except Exception:
            pass

        # 兜底找提交
        for text in ["提交", "交卷", "确认提交", "提交答案"]:
            try:
                btn = self.page.get_by_text(text)
                if btn.is_visible(timeout=1000):
                    btn.click()
                    self.logger.info(f"点击「{text}」")
                    return
            except Exception:
                continue

        self.logger.warn("未找到「下一题」或「提交」按钮")
        self.logger.save_screenshot(self.page, "no_next_button")

    def _submit_and_wait(self) -> bool:
        """点击提交并等待确认对话框/结果页面。返回 True 表示提交完成。"""
        # 1. 点击提交
        submitted = False
        for text in ["提交", "交卷", "提交答案", "完成"]:
            try:
                btn = self.page.get_by_text(text)
                if btn.is_visible(timeout=2000):
                    btn.click()
                    self.logger.info(f"点击「{text}」（最后一道题）")
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            self.logger.warn("未找到提交按钮")
            return False

        # 2. 处理确认弹窗
        self.page.wait_for_timeout(2000)
        try:
            # 优先用 button role（避免匹配到标题文字）
            confirm_btn = self.page.get_by_role("button", name="确认提交")
            if confirm_btn.is_visible(timeout=5000):
                confirm_btn.click()
                self.logger.info("点击确认提交按钮")
                self.page.wait_for_timeout(3000)
            else:
                # 备选：点击 get_by_text 的最后一个（button 通常在后）
                for text in ["确认提交", "确认", "确定", "是"]:
                    try:
                        btn = self.page.get_by_text(text, exact=True).last
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            self.logger.info(f"点击确认「{text}」")
                            self.page.wait_for_timeout(3000)
                            break
                    except Exception:
                        continue
        except Exception as e:
            self.logger.warn(f"处理确认弹窗异常: {e}")

        # 3. 等待结果页面
        self.page.wait_for_timeout(3000)
        content = self.page.content()
        if any(kw in content for kw in ["提交成功", "完成", "成绩", "得分"]):
            self.logger.info("提交成功!")
            return True

        self.logger.info("提交后未明确检测到完成，但已执行提交流程")
        return True

    # ── 重试机制 ───────────────────────────────────────────

    def _collect_answer_key(self) -> dict:
        """
        在结果页面点击「查看解析」，遍历所有题目收集正确答案。
        返回 {question_index: correct_letter} 如 {1: 'B', 2: 'C', ...}
        """
        self.logger.info("点击「查看解析」获取正确答案...")
        try:
            self.page.get_by_text("查看解析").click(timeout=5000)
        except Exception:
            self.logger.warn("无法点击「查看解析」")
            return {}

        self.page.wait_for_timeout(2000)
        answer_key = {}

        for i in range(self.total_questions or 10):
            self.page.wait_for_timeout(1000)
            body = self._get_page_text()

            # 解析正确答案（✓ 标记）
            correct_letter = self._parse_correct_answer(body)
            if correct_letter:
                answer_key[i + 1] = correct_letter
                self.logger.info(f"第 {i+1} 题正确答案: {correct_letter}")

            # 点击下一题
            try:
                btn = self.page.get_by_text("下一题")
                if btn.is_visible(timeout=2000):
                    btn.click()
                else:
                    break
            except Exception:
                break

        self.logger.info(f"正确答案收集完成: {answer_key}")
        return answer_key

    def _parse_correct_answer(self, body: str) -> str | None:
        """从解析页面文本中提取正确答案字母。查找 ✓ 后面的选项。"""
        lines = body.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            # 找到了 ✓
            if stripped == "✓" and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                match = re.match(r"([A-D])", next_line)
                if match:
                    return match.group(1)
                # 可能 ✓ 在选项后面一行
                for j in range(i + 1, min(i + 4, len(lines))):
                    check_line = lines[j].strip()
                    m = re.match(r"([A-D])[.、:：\s]", check_line)
                    if m:
                        return m.group(1)
        return None

    def _click_restart(self):
        """重新开始答题——导航回 SSO 链接刷新状态。"""
        self.logger.info("重新加载每日一练...")
        self.page.goto(self._practice_url, wait_until="domcontentloaded",
                       timeout=config.BROWSER_LONG_TIMEOUT)
        self._wait_for_app_ready()
        self._check_page_state()
        self._click_start_if_needed()
        self.question_count = 0

    def _retry_with_answers(self, answer_key: dict):
        """使用已知正确答案重新答题。"""
        self.logger.info("使用正确答案重新答题...")

        for i in range(self.total_questions or 50):
            self.question_count += 1
            idx = self.question_count

            self.page.wait_for_timeout(1500)

            body = self._get_page_text()
            q_data = self._parse_question(body)
            if not q_data:
                self.logger.error(f"第 {idx} 题 DOM 解析失败")
                continue

            question = q_data["question"]
            options = q_data["options"]
            correct_letter = answer_key.get(idx)

            if correct_letter:
                self.logger.info(f"第 {idx} 题: 使用已知答案 {correct_letter}")
            else:
                # 没有已知答案，题库优先
                bank_r = bank_find_answer(question, options)
                if bank_r:
                    correct_letter = bank_r["answer"]
                    self.logger.info(f"第 {idx} 题: 题库命中 {bank_r['answer']}")
                else:
                    # 最后用 LLM
                    self.logger.info(f"第 {idx} 题: 题库未命中，使用 LLM")
                    try:
                        result = llm_answer(question, options)
                        correct_letter = result["answer"]
                    except Exception:
                        correct_letter = "A"

            self._click_option(correct_letter, options)

            self.page.wait_for_timeout(800)
            is_last = (idx >= self.total_questions) if self.total_questions else False
            if is_last:
                self._submit_and_wait()
                break
            else:
                self._click_next_or_submit(is_last=False)

        self.logger.info(f"重试循环结束，共处理 {self.question_count} 题")

    # ── 提交确认 ───────────────────────────────────────────

    def _confirm_submission(self):
        """处理提交确认弹窗。"""
        self.page.wait_for_timeout(1500)
        for text in ["确认", "确定", "确认提交", "是"]:
            try:
                btn = self.page.get_by_text(text, exact=True)
                if btn.is_visible(timeout=2000):
                    btn.click()
                    self.logger.info(f"点击确认「{text}」")
                    self.page.wait_for_timeout(2000)
                    return
            except Exception:
                continue

    def _verify_completion(self):
        self.page.wait_for_timeout(3000)
        self.logger.save_screenshot(self.page, "completion")

        url = self.page.url
        content = self.page.content()
        body = self._get_page_text()

        # URL 包含结果页面
        if "/answer/result" in url or "/result" in url:
            self.logger.info(f"已进入结果页面: {body[:120]}")
            if "答错0道" in body or "答错 0 道" in body:
                self.logger.info("每日一练通过（全对）!")
            elif "答错" in body or "答对" in body:
                self.logger.info(f"每日一练已提交，成绩: {body[:80]}")
            return True

        # 包含结果关键词
        if any(kw in content for kw in ["提交成功", "完成", "成绩", "得分"]):
            self.logger.info("每日一练完成!")
            return True

        if "查看解析" in body or "重新作答" in body:
            self.logger.info(f"结果页面: {body[:120]}")
            return True

        self.logger.warn("未明确检测到完成状态，请检查截图")
        return True
