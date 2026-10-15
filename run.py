"""Run daily practice for the configured account, or a serial account batch."""
import sys
import os
import fcntl
import traceback
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
LOCK_FILE = '/tmp/daily_practice.lock' if config.STATE_DIR == config.BASE_DIR else str(config.STATE_DIR / 'run.lock')
_lock_fd = None

def acquire_lock():
    pass
    global _lock_fd
    Path(LOCK_FILE).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (IOError, BlockingIOError):
        print(f'已有实例正在运行 (锁文件: {LOCK_FILE})，退出。')
        return False

def release_lock():
    global _lock_fd
    if _lock_fd:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
        except Exception:
            pass
        _lock_fd = None
from logger import Logger
from browser_manager import BrowserManager
from auth import authenticate, try_direct_access, save_token_cache
from question_bank import get_bank, append_question as bank_append
from daily_practice import DailyPracticeRunner
from notify import send as notify_send
from exceptions import DailyPracticeError, NoQuestionsTodayError, AlreadyCompletedError, NetworkError, LoginError

def main():
    if not acquire_lock():
        sys.exit(0)
    required = ('EMPLOYEE_ID', 'PASSWORD', 'LOGIN_URL', 'SSO_REDIRECT_URL', 'APP_ID', 'APP_ID_STR', 'H5_BASE')
    if any(not getattr(config, key) for key in required):
        print('错误: 请按照 .env.example 配置账号和门户应用参数')
        release_lock()
        sys.exit(1)
    logger = Logger()
    dry_run = '--dry-run' in sys.argv
    try:
        if dry_run:
            logger.stage('authentication')
            user_data, sso_data, practice_url = authenticate()
            logger.info(f"登录成功: {user_data.get('chineseName', '')} ({user_data.get('userName', '')})")
            save_token_cache(sso_data['appAccessToken'])
            logger.info('账号与每日一练 SSO 验证通过')
            logger.info('干运行完成，未实际进入浏览器。')
            logger.summary(True, 0, '干运行')
            return
        logger.stage('bank_load')
        bank = get_bank()
        logger.info(f'题库加载完成: {bank.single_choice_count} 单选 + {bank.true_false_count} 判断')
        logger.stage('browser_launch')
        with BrowserManager() as (browser_mgr, page):
            logger.info('浏览器已启动')
            session_ok = try_direct_access(page)
            relogin = False
            if session_ok:
                logger.info('持久化 session 有效，跳过 API 登录')
                practice_url = None
            else:
                relogin = True
                logger.stage('authentication')
                logger.info('session 已过期，重新登录...')
                user_data, sso_data, practice_url = authenticate()
                logger.info(f"登录成功: {user_data.get('chineseName', '')} ({user_data.get('userName', '')})")
                save_token_cache(sso_data['appAccessToken'])
                page.goto(practice_url, wait_until='domcontentloaded', timeout=config.BROWSER_LONG_TIMEOUT)
                try:
                    page.wait_for_load_state('networkidle', timeout=config.BROWSER_LONG_TIMEOUT)
                except Exception:
                    pass
                browser_mgr.save_state()
                logger.info('浏览器状态已保存，下次可复用')
            logger.stage('daily_practice')
            runner = DailyPracticeRunner(page, logger)
            runner.run(practice_url)
            logger.stage('complete')
            logger.summary(True, runner.question_count, '每日一练完成')
            login_info = '重新登录' if relogin else '复用session'
            if runner.retry_count > 0:
                detail = f'登录: {login_info} | 重试: {runner.retry_count}次 | 答错: {runner.wrong_count}题 | 题库: {runner.bank_hits}/{runner.question_count} | LLM: {runner.llm_hits}/{runner.question_count}'
            else:
                detail = f'登录: {login_info} | 一次通过 | 题库: {runner.bank_hits}/{runner.question_count} | LLM: {runner.llm_hits}/{runner.question_count}'
            notify_send('每日一练完成', f'答题 {runner.question_count} 题，全部通过 ✅\n{detail}')
            added = 0
            for item in runner.new_bank_items:
                bank_append(item['question'], item['options'], item['answer'], item.get('qtype', ''))
                added += 1
            if added:
                logger.info(f'题库新增 {added} 道 LLM 答对题目')
    except AlreadyCompletedError as e:
        logger.info(str(e))
        logger.summary(True, 0, 'already_completed')
        notify_send('每日一练', str(e))
        sys.exit(0)
    except NoQuestionsTodayError as e:
        logger.info(str(e))
        logger.summary(True, 0, 'no_questions')
        notify_send('每日一练', str(e))
        sys.exit(0)
    except LoginError as e:
        logger.error(f'登录失败: {e}')
        logger.summary(False, 0, 'login_failed')
        notify_send('每日一练失败', '登录失败，请检查密码或网络')
        sys.exit(3)
    except NetworkError as e:
        logger.error(f'网络不可达: {e}')
        logger.error('请检查是否在沙钢内网，或 VPN 是否连接。')
        logger.summary(False, 0, 'network_error')
        notify_send('每日一练失败', f'网络不可达: {e}')
        sys.exit(2)
    except DailyPracticeError as e:
        logger.error(f'执行异常: {e}')
        logger.error(traceback.format_exc())
        logger.summary(False, 0, 'error')
        notify_send('每日一练失败', f'执行异常: {e}')
        sys.exit(4)
    except Exception as e:
        logger.error(f'未预期的错误: {e}')
        logger.error(traceback.format_exc())
        logger.summary(False, 0, 'unexpected_error')
        notify_send('每日一练失败', f'异常: {e}')
        sys.exit(4)
    finally:
        logger.flush()
        release_lock()
if __name__ == '__main__':
    if os.environ.get('DAILY_PRACTICE_ACCOUNT_RUN') != '1':
        from account_runner import main as run_accounts
        sys.exit(run_accounts(sys.argv[1:]))
    os.umask(0o077)
    main()
