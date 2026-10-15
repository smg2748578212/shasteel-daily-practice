import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time


class Worker:
    def __init__(self,store):
        self.store = store
        self.thread = None
        self.stop_event = threading.Event()
        self.process = None

    def start(self):
        self.lock = (self.store.root/'worker.lock').open('a+')
        try:
            fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('An execution scheduler is already running')
        self.store.recover()
        self.thread = threading.Thread(target=self.loop,name='daily-practice-scheduler',daemon=True)
        self.thread.start()

    def loop(self):
        next_schedule = 0
        while not self.stop_event.is_set():
            try:
                if time.monotonic() >= next_schedule:
                    if os.environ.get('DAILY_MANAGER_PAUSED') != '1':
                        self.store.schedule()
                    next_schedule = time.monotonic()+30
                job = self.store.claim()
                if job:
                    self.execute(job)
                    continue
            except Exception as error:
                print('Scheduler operation failed:',type(error).__name__,flush=True)
            self.stop_event.wait(2)

    def execute(self,job):
        result = None
        try:
            employee,name,password = self.store.credentials(job['account_id'])
            state = self.store.root/'accounts'/str(job['account_id'])
            state.mkdir(parents=True,exist_ok=True,mode=0o700)
            env = dict(os.environ,SHASTEEL_EMPLOYEE_ID=employee,SHASTEEL_EMPLOYEE_NAME=name,
                       SHASTEEL_PASSWORD=password,DAILY_PRACTICE_STATE_DIR=str(state),PYTHONUNBUFFERED='1')
            env.pop('DATABASE_URL',None)
            self.process = subprocess.Popen([sys.executable,'-u','-m','management.execute',job['mode']],
                                            cwd=Path(__file__).resolve().parents[1],env=env,
                                            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
            selector = selectors.DefaultSelector()
            selector.register(self.process.stdout,selectors.EVENT_READ)
            deadline = time.monotonic()+1200
            while time.monotonic()<deadline and not self.stop_event.is_set():
                events = selector.select(timeout=1)
                if not events and self.process.poll() is not None:
                    break
                for key,_ in events:
                    line = key.fileobj.readline()
                    if not line:
                        selector.unregister(key.fileobj)
                        break
                    try:
                        event = json.loads(line)
                    except (ValueError,TypeError):
                        continue
                    if event.get('type')=='progress' and event.get('stage') in ('authenticating','checking','answering','verifying'):
                        self.store.progress(job['id'],event['stage'],event.get('current',0),event.get('total',0))
                    elif event.get('type')=='result':
                        result = event
                if not selector.get_map():
                    break
            selector.close()
            if self.process.poll() is None:
                os.killpg(self.process.pid,signal.SIGTERM)
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid,signal.SIGKILL)
                    self.process.wait(timeout=5)
            if result:
                self.store.finish(job['id'],completed=result.get('completed'),error=result.get('error'))
            else:
                self.store.finish(job['id'],error='执行超时或进程中断，请重新检查完成状态')
        except Exception as error:
            self.store.finish(job['id'],error='任务执行失败（'+type(error).__name__+'）')
        finally:
            self.process = None
