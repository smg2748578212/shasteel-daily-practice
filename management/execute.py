"""A child process for one account. Standard output contains only safe events."""
import json
import os
import sys
from urllib.parse import urlsplit

import config
from auth import authenticate, save_token_cache, try_direct_access
from browser_manager import BrowserManager
from daily_practice import DailyPracticeRunner
from exceptions import AlreadyCompletedError, LoginError, NetworkError, NoQuestionsTodayError
from logger import Logger
from question_bank import append_question, get_bank


def emit(**event):
    print(json.dumps(event,ensure_ascii=False),flush=True)


class ProgressLogger(Logger):
    def __init__(self):
        super().__init__()
        self.runner = None

    def _entry(self, level, message, **extra):
        super()._entry(level,message,**extra)
        runner = self.runner
        emit(type='progress',stage=self._current_stage or 'answering',
             current=runner.question_count if runner else 0,
             total=runner.total_questions if runner else 0)


def verify_completion(page):
    endpoint = config.COMPLETION_API_URL
    if not endpoint:
        raise ValueError('Completion endpoint is not configured')
    result = page.evaluate('''async endpoint => {
      const token=localStorage.getItem('MRYLToken');
      if(!token) throw new Error('No authenticated session');
      const response=await fetch(endpoint,{method:'POST',headers:{Authorization:token}});
      if(!response.ok) throw new Error('Completion request failed');
      return await response.json();
    }''',endpoint)
    if result.get('code') != 200 or type(result.get('data')) is not bool:
        raise ValueError('Completion response was not authoritative')
    return result['data']


def main(mode):
    os.umask(0o077)
    logger = ProgressLogger()
    logger.stage('authenticating')
    with BrowserManager() as (browser,page):
        if not try_direct_access(page):
            _, sso, url = authenticate()
            save_token_cache(sso['appAccessToken'])
            page.goto(url,wait_until='domcontentloaded',timeout=config.BROWSER_LONG_TIMEOUT)
            page.wait_for_load_state('networkidle',timeout=config.BROWSER_LONG_TIMEOUT)
        browser.save_state()
        logger.stage('checking')
        completed = verify_completion(page)
        if mode == 'check' or completed:
            emit(type='result',completed=completed)
            return
        get_bank()
        logger.stage('answering')
        runner = DailyPracticeRunner(page,logger)
        logger.runner = runner
        try:
            runner.run()
        except AlreadyCompletedError:
            pass
        logger.stage('verifying')
        # The original result-page heuristics may return success after a failed
        # attempt. Only the portal's explicit completion boolean is authoritative.
        completed = verify_completion(page)
        browser.save_state()
        if completed:
            for item in runner.new_bank_items:
                append_question(item['question'],item['options'],item['answer'],item.get('qtype',''))
            emit(type='result',completed=True)
        else:
            emit(type='result',completed=False,error='门户尚未确认完成，请查看执行记录后重试')


if __name__ == '__main__':
    try:
        main(sys.argv[1] if len(sys.argv)>1 else 'check')
    except LoginError:
        emit(type='result',error='门户登录失败，请更新该人员的登录信息')
    except NetworkError:
        emit(type='result',error='门户网络连接失败，请稍后重试')
    except NoQuestionsTodayError:
        emit(type='result',error='门户今日暂无题目，未记为已完成')
    except Exception as error:
        emit(type='result',error='执行失败（'+type(error).__name__+'），请稍后重试或检查服务日志')
