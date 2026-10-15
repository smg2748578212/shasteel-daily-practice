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
from management.reports import format_daily_report
from management.roster import build_roster

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
              execution_mode TEXT NOT NULL DEFAULT 'run',
              enrollment_source TEXT NOT NULL DEFAULT 'admin', public_enrolled_at TEXT,
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
            CREATE INDEX IF NOT EXISTS account_day_jobs ON jobs(account_id,day,created_at DESC);
            CREATE TABLE IF NOT EXISTS manager_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS daily_reports (
              day TEXT PRIMARY KEY,status TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,
              created_at TEXT NOT NULL,finished_at TEXT);
            ''')
            # Existing installations retain their previously configured answering behavior.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(accounts)')}
            if 'execution_mode' not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'run'")
            if 'enrollment_source' not in columns:
                db.execute("ALTER TABLE accounts ADD COLUMN enrollment_source TEXT NOT NULL DEFAULT 'admin'")
            if 'public_enrolled_at' not in columns:
                db.execute('ALTER TABLE accounts ADD COLUMN public_enrolled_at TEXT')
            report_columns = {row['name'] for row in db.execute('PRAGMA table_info(daily_reports)')}
            if 'roster_json' not in report_columns:
                db.execute('ALTER TABLE daily_reports ADD COLUMN roster_json TEXT')
            at = now()
            first_report = (at + timedelta(days=1) if at.hour >= 16 else at).date().isoformat()
            db.execute("INSERT OR IGNORE INTO manager_settings(key,value) VALUES('daily_summary_start_day',?)",(first_report,))
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

    @staticmethod
    def roster_people(db, day):
        return [dict(row) for row in db.execute('''
          SELECT a.employee_name,a.employee_id,a.team,a.enabled,a.checked_day,a.completion,a.checked_at,
                 j.status AS last_status,j.finished_at AS last_finished_at
          FROM accounts a LEFT JOIN jobs j ON j.rowid=(
            SELECT rowid FROM jobs WHERE account_id=a.id AND day=?
            ORDER BY created_at DESC,rowid DESC LIMIT 1)
          WHERE a.deleted=0 ORDER BY a.team,a.employee_name,a.id
        ''',(day,)).fetchall()]

    def roster(self, day=None):
        today = now().date().isoformat()
        if day is not None:
            if not isinstance(day,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',day):
                raise ValueError('日期格式为 YYYY-MM-DD')
            try:
                datetime.strptime(day,'%Y-%m-%d')
            except ValueError:
                raise ValueError('日期不正确') from None
            if day > today:
                raise ValueError('不能查看未来日期的名单')
        with self.connect() as db:
            if day is not None:
                report = db.execute('SELECT roster_json FROM daily_reports WHERE day=?',(day,)).fetchone()
                if report and report['roster_json']:
                    return json.loads(report['roster_json'])
                if day != today:
                    raise LookupError('该日期尚未保存名单快照')
            day = day or today
            return build_roster(self.roster_people(db,day),day,timestamp())

    def save(self, data, account_id=None, *, public_enrollment=False):
        if not isinstance(data, dict):
            raise ValueError('人员信息必须为 JSON 对象')
        allowed = {'employee_id','employee_name','team','password','enabled','run_time','run_days','execution_mode'}
        if set(data) - allowed:
            raise ValueError('包含不支持的人员字段')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0',(account_id,)).fetchone() if account_id else None
            if public_enrollment:
                old = db.execute('SELECT * FROM accounts WHERE employee_id=?',(data.get('employee_id'),)).fetchone()
                if old and old['deleted']:
                    raise ValueError('该账号暂时无法接入，请联系管理员')
                account_id = old['id'] if old else None
            if account_id and not old:
                raise LookupError('人员不存在')
            values = dict(old) if old else {}
            values.update(data)
            source = 'public' if public_enrollment else values.get('enrollment_source','admin')
            enrolled_at = values.get('public_enrolled_at')
            if public_enrollment:
                name = old['employee_name'] if old and old['employee_name'] != old['employee_id'] else data.get('employee_name',data.get('employee_id'))
                values.update(employee_name=name,
                              execution_mode='check',run_time='16:00',enabled=True)
                enrolled_at = enrolled_at or timestamp()
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
            execution_mode = values.get('execution_mode','check')
            if execution_mode not in ('check','run'):
                raise ValueError('请选择仅查看进度或完成每日一练')
            if execution_mode == 'check' and db.execute(
                "SELECT 1 FROM jobs WHERE account_id=? AND status='running' AND mode='run'",(account_id,)
            ).fetchone():
                raise ValueError('答题任务正在执行，请结束后切换为仅查看进度')
            run_time = values.get('run_time','07:00')
            if not isinstance(run_time,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',run_time):
                raise ValueError('执行时间格式为 HH:MM')
            days = list(range(7)) if public_enrollment else data.get('run_days',json.loads(old['run_days']) if old else list(range(7)))
            if not isinstance(days,list) or not days or any(type(d) is not int or d not in range(7) for d in days):
                raise ValueError('请选择有效的执行星期')
            if source == 'public' and (execution_mode != 'check' or run_time != '16:00' or set(days) != set(range(7))):
                raise ValueError('自助接入人员固定为仅查看进度，每天 16:00 查询')
            password = data.get('password','')
            if not isinstance(password,str) or len(password)>256 or '\0' in password:
                raise ValueError('密码格式不正确')
            if not password and not old:
                raise ValueError('新增人员需要提供门户密码')
            secret = self.cipher.encrypt(password.encode()).decode() if password else old['secret']
            current = timestamp()
            if old:
                db.execute('UPDATE accounts SET employee_name=?,team=?,secret=?,enabled=?,execution_mode=?,run_time=?,run_days=?,enrollment_source=?,public_enrolled_at=?,updated_at=? WHERE id=?',
                           (name.strip(),team.strip(),secret,int(enabled),execution_mode,run_time,json.dumps(sorted(set(days))),source,enrolled_at,current,account_id))
                if execution_mode == 'check':
                    db.execute("UPDATE jobs SET mode='check' WHERE account_id=? AND status='queued' AND mode='run'",(account_id,))
                if not enabled:
                    db.execute("UPDATE jobs SET status='interrupted',stage='finished',message='计划已暂停，排队任务已取消',finished_at=? WHERE account_id=? AND status='queued'",(current,account_id))
            else:
                cursor = db.execute('INSERT INTO accounts(employee_id,employee_name,team,secret,enabled,execution_mode,run_time,run_days,enrollment_source,public_enrolled_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                    (employee,name.strip(),team.strip(),secret,int(enabled),execution_mode,run_time,json.dumps(sorted(set(days))),source,enrolled_at,current,current))
                account_id = cursor.lastrowid
            return self.public(db.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone())

    def delete(self, account_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE account_id=? AND status IN ('queued','running')",(account_id,)).fetchone():
                raise ValueError('人员有正在执行或排队的任务，请结束后再移除')
            if not db.execute('UPDATE accounts SET deleted=1,enabled=0,secret=\'\',updated_at=? WHERE id=? AND deleted=0',(timestamp(),account_id)).rowcount:
                raise LookupError('人员不存在')

    def enqueue(self, account_id, mode=None, trigger='manual', at=None):
        if mode is not None and mode not in ('run','check'):
            raise ValueError('任务类型不正确')
        at = at or now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            account = db.execute('SELECT * FROM accounts WHERE id=? AND deleted=0',(account_id,)).fetchone()
            if not account:
                raise LookupError('人员不存在')
            if not account['enabled']:
                raise ValueError('人员已暂停，请先启用')
            mode = mode or account['execution_mode']
            if mode == 'run' and (account['execution_mode'] == 'check' or account['enrollment_source'] == 'public'):
                raise ValueError('该人员配置为仅查看进度，不能执行答题')
            job_id = str(uuid.uuid4())
            try:
                db.execute('INSERT INTO jobs(id,account_id,day,mode,trigger,status,created_at) VALUES(?,?,?,?,?,\'queued\',?)',
                           (job_id,account_id,at.date().isoformat(),mode,trigger,at.isoformat(timespec='seconds')))
            except sqlite3.IntegrityError:
                return None
            return job_id

    @staticmethod
    def due(account, at):
        if not account['enabled'] or at.weekday() not in account['run_days']:
            return False
        if account['run_time'] > at.strftime('%H:%M'):
            return False
        if account['enrollment_source']=='public' and account['public_enrolled_at']:
            due = at.replace(hour=16,minute=0,second=0,microsecond=0)
            if datetime.fromisoformat(account['public_enrolled_at']) > due:
                return False
        return not (account['execution_mode']=='run' and account['checked_day']==at.date().isoformat() and account['completion'])

    def schedule(self, at=None):
        at = at or now()
        for account in self.accounts():
            if self.due(account,at):
                self.enqueue(account['id'],trigger='schedule',at=at)

    def claim_daily_report(self, at=None):
        at = at or now()
        day = at.date().isoformat()
        if at.hour < 16:
            return None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            first_day = db.execute("SELECT value FROM manager_settings WHERE key='daily_summary_start_day'").fetchone()[0]
            if day < first_day or db.execute('SELECT 1 FROM daily_reports WHERE day=?',(day,)).fetchone():
                return None
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone():
                return None
            rows = db.execute('SELECT * FROM accounts WHERE deleted=0 AND enabled=1 ORDER BY team,employee_name,id').fetchall()
            if not rows:
                return None
            for row in rows:
                if self.due(self.public(row),at) and not db.execute("SELECT 1 FROM jobs WHERE account_id=? AND day=? AND trigger='schedule'",(row['id'],day)).fetchone():
                    return None
            all_people = self.roster_people(db,day)
            people = [person for person in all_people if person['enabled']]
            base = os.environ.get('DAILY_MANAGER_PUBLIC_URL','').rstrip('/')
            roster_url = f'{base}/daily-practice-roster.html?date={day}'
            report = format_daily_report(people,day,roster_url)
            snapshot = build_roster(all_people,day,timestamp(),source='snapshot')
            db.execute("INSERT INTO daily_reports(day,status,title,content,created_at,roster_json) VALUES(?,'sending',?,?,?,?)",(day,report['title'],report['content'],timestamp(),json.dumps(snapshot,ensure_ascii=False)))
            return dict(day=day,**report)

    def finish_daily_report(self, day, sent):
        with self.connect() as db:
            db.execute("UPDATE daily_reports SET status=?,finished_at=? WHERE day=? AND status='sending'",('sent' if sent else 'failed',timestamp(),day))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT j.* FROM jobs j JOIN accounts a ON a.id=j.account_id WHERE j.status='queued' AND a.enabled=1 AND a.deleted=0 ORDER BY j.created_at,j.rowid LIMIT 1").fetchone()
            if row:
                # Recheck the saved mode inside the same transaction used to claim the job.
                account = db.execute('SELECT execution_mode,enrollment_source FROM accounts WHERE id=?',(row['account_id'],)).fetchone()
                if row['mode'] == 'run' and (account['execution_mode'] == 'check' or account['enrollment_source'] == 'public'):
                    db.execute("UPDATE jobs SET mode='check' WHERE id=?",(row['id'],))
                    row = db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone()
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
