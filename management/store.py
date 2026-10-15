import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

TZ = ZoneInfo('Asia/Shanghai')
ACTIVE = ('queued', 'running')


def now():
    return datetime.now(TZ)


def timestamp():
    return now().isoformat(timespec='seconds')


def next_execution(account, at=None):
    if not account['enabled'] or account.get('deleted'):
        return None
    at = at or now()
    hour, minute = map(int, account['run_time'].split(':'))
    for offset in range(8):
        day = at + timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > at and candidate.weekday() in account['run_days']:
            return candidate.isoformat(timespec='seconds')
    return None


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = self.root / 'credentials.key'
        try:
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if key.is_symlink():
                raise ValueError('Invalid credential key path')
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        self.path = self.root / 'manager.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS accounts (
              id INTEGER PRIMARY KEY, employee_id TEXT NOT NULL UNIQUE,
              employee_name TEXT NOT NULL, team TEXT NOT NULL DEFAULT '',
              secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              run_time TEXT NOT NULL, run_days TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              checked_day TEXT, completion INTEGER, checked_at TEXT);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
              day TEXT NOT NULL, mode TEXT NOT NULL, trigger TEXT NOT NULL,
              status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued',
              current INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
              message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
              started_at TEXT, finished_at TEXT, verified INTEGER);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(account_id)
              WHERE status IN ('queued','running');
            CREATE UNIQUE INDEX IF NOT EXISTS one_scheduled_run ON jobs(account_id,day)
              WHERE trigger='schedule';
            CREATE INDEX IF NOT EXISTS recent_jobs ON jobs(created_at);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def public(row):
        account = dict(row)
        account['password_set'] = bool(account.pop('secret'))
        account['run_days'] = json.loads(account['run_days'])
        account['enabled'] = bool(account['enabled'])
        account['next_run'] = next_execution(account)
        return account

    def accounts(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM accounts WHERE deleted=0 ORDER BY team,employee_name,id').fetchall()
            result = []
            for row in rows:
                item = self.public(row)
                latest = db.execute('SELECT * FROM jobs WHERE account_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (row['id'],)).fetchone()
                item['latest_job'] = dict(latest) if latest else None
                item['today_status'] = ('completed' if item['completion'] else 'pending') if item['checked_day'] == now().date().isoformat() else 'unknown'
                result.append(item)
            return result

    def save(self, data, account_id=None):
        if not isinstance(data, dict):
            raise ValueError('人员信息必须为 JSON 对象')
        allowed = {'employee_id','employee_name','team','password','enabled','run_time','run_days'}
        if set(data) - allowed:
            raise ValueError('包含不支持的人员字段')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0',(account_id,)).fetchone() if account_id else None
            if account_id and not old:
                raise LookupError('人员不存在')
            values = dict(old) if old else {}
            values.update(data)
            employee = values.get('employee_id','')
            name = values.get('employee_name','')
            team = values.get('team','')
            if not isinstance(employee,str) or not re.fullmatch(r'[0-9]{1,20}',employee):
                raise ValueError('工号需为 1–20 位数字')
            if old and employee != old['employee_id']:
                raise ValueError('工号不可修改，请新增人员')
            if not isinstance(name,str) or not name.strip() or len(name)>80:
                raise ValueError('请填写姓名，最多 80 个字符')
            if not isinstance(team,str) or len(team)>80:
                raise ValueError('班组名称最多 80 个字符')
            enabled = values.get('enabled',True)
            if type(enabled) not in (bool,int) or enabled not in (0,1):
                raise ValueError('启用状态不正确')
            run_time = values.get('run_time','07:00')
            if not isinstance(run_time,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',run_time):
                raise ValueError('执行时间格式为 HH:MM')
            days = data.get('run_days',json.loads(old['run_days']) if old else list(range(7)))
            if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(7) for d in days):
                raise ValueError('请选择有效的执行星期')
            password = data.get('password','')
            if not isinstance(password,str) or len(password)>256 or '\0' in password:
                raise ValueError('密码格式不正确')
            if not password and not old:
                raise ValueError('新增人员需要提供门户密码')
            secret = self.cipher.encrypt(password.encode()).decode() if password else old['secret']
            current = timestamp()
            if old:
                db.execute('UPDATE accounts SET employee_name=?,team=?,secret=?,enabled=?,run_time=?,run_days=?,updated_at=? WHERE id=?',
                           (name.strip(),team.strip(),secret,int(enabled),run_time,json.dumps(sorted(set(days))),current,account_id))
                if not enabled:
                    db.execute("UPDATE jobs SET status='interrupted',stage='finished',message='计划已暂停，排队任务已取消',finished_at=? WHERE account_id=? AND status='queued'",(current,account_id))
            else:
                cursor = db.execute('INSERT INTO accounts(employee_id,employee_name,team,secret,enabled,run_time,run_days,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                                    (employee,name.strip(),team.strip(),secret,int(enabled),run_time,json.dumps(sorted(set(days))),current,current))
                account_id = cursor.lastrowid
            return self.public(db.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone())

    def delete(self, account_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE account_id=? AND status IN ('queued','running')",(account_id,)).fetchone():
                raise ValueError('人员有正在执行或排队的任务，请结束后再移除')
            if not db.execute('UPDATE accounts SET deleted=1,enabled=0,secret=\'\',updated_at=? WHERE id=? AND deleted=0',(timestamp(),account_id)).rowcount:
                raise LookupError('人员不存在')

    def enqueue(self, account_id, mode='run', trigger='manual', at=None):
        if mode not in ('run','check'):
            raise ValueError('任务类型不正确')
        at = at or now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0',(account_id,)).fetchone()
            if not account:
                raise LookupError('人员不存在')
            if not account['enabled']:
                raise ValueError('人员已暂停，请先启用')
            job_id = str(uuid.uuid4())
            try:
                db.execute('INSERT INTO jobs(id,account_id,day,mode,trigger,status,created_at) VALUES(?,?,?,?,?,\'queued\',?)',
                           (job_id,account_id,at.date().isoformat(),mode,trigger,at.isoformat(timespec='seconds')))
            except sqlite3.IntegrityError:
                return None
            return job_id

    def schedule(self, at=None):
        at = at or now()
        for account in self.accounts():
            if not account['enabled'] or at.weekday() not in account['run_days']:
                continue
            if account['run_time'] > at.strftime('%H:%M'):
                continue
            if account['checked_day']==at.date().isoformat() and account['completion']:
                continue
            self.enqueue(account['id'],trigger='schedule',at=at)

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT j.* FROM jobs j JOIN accounts a ON a.id=j.account_id WHERE j.status='queued' AND a.enabled=1 AND a.deleted=0 ORDER BY j.created_at,j.rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='running',stage='starting',started_at=? WHERE id=?",(timestamp(),row['id']))
            return dict(row) if row else None

    def credentials(self, account_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0',(account_id,)).fetchone()
        if not row:
            raise LookupError('人员不存在')
        return row['employee_id'], row['employee_name'], self.cipher.decrypt(row['secret'].encode()).decode()

    def progress(self, job_id, stage, current=0, total=0):
        with self.connect() as db:
            db.execute('UPDATE jobs SET stage=?,current=?,total=? WHERE id=? AND status=\'running\'',(stage,max(0,int(current)),max(0,int(total)),job_id))

    def finish(self, job_id, completed=None, error=None):
        with self.connect() as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not job:
                raise LookupError('任务不存在')
            status = 'failed' if error else ('completed' if completed else 'pending')
            message = error or ('门户已确认今日完成' if completed else '门户显示今日尚未完成')
            db.execute('UPDATE jobs SET status=?,stage=\'finished\',message=?,finished_at=?,verified=? WHERE id=?',
                       (status,message,timestamp(),None if completed is None else int(completed),job_id))
            if completed is not None:
                db.execute('UPDATE accounts SET checked_day=?,completion=?,checked_at=? WHERE id=?',
                           (now().date().isoformat(),int(completed),timestamp(),job['account_id']))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',stage='finished',message='服务重启，执行中断；可重新检查或执行',finished_at=? WHERE status='running'",(timestamp(),))
            db.execute("UPDATE jobs SET status='interrupted',stage='finished',message='排队任务已过期，请重新执行',finished_at=? WHERE status='queued' AND day<>?",(timestamp(),now().date().isoformat()))

    def history(self, account_id=None, limit=50):
        with self.connect() as db:
            condition = ' WHERE j.account_id=?' if account_id else ''
            args = [account_id] if account_id else []
            rows = db.execute('SELECT j.*,a.employee_name,a.employee_id,a.team FROM jobs j JOIN accounts a ON a.id=j.account_id'+condition+' ORDER BY j.created_at DESC,j.rowid DESC LIMIT ?',args+[limit]).fetchall()
            return [dict(row) for row in rows]
