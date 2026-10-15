"""Private loopback API using the existing site's administrator session."""
import os
from pathlib import Path
import sqlite3
import sys
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

from management.store import Store, now
from management.worker import Worker
from management.enrollment import EnrollmentError, EnrollmentLimiter, parse_submission, verify_portal_credentials


def create_app(root=None,authenticator=None,start_worker=False):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16384
    store = Store(root or os.environ.get('DAILY_MANAGER_DATA','./runtime'))
    worker = Worker(store)
    enrollment_limiter = EnrollmentLimiter()
    app.extensions['daily_store'] = store
    app.extensions['daily_worker'] = worker
    if authenticator is None:
        sys.path.insert(0,os.environ.get('SITE_SHARED_ROOT','/opt/webapp'))
        from shared.models import db
        from shared.database import configure_db
        from shared.auth import create_token_required
        os.environ.setdefault('DB_DAILY_MANAGER_POOL_SIZE','1')
        os.environ.setdefault('DB_DAILY_MANAGER_MAX_OVERFLOW','0')
        configure_db(app,'daily_manager',include_sg=False)
        db.init_app(app)
        session_required = create_token_required(page_ids={'daily-practice'},release_session=True)
        def authenticator(function):
            @wraps(function)
            @session_required
            def authenticated(*args,**kwargs):
                if request.current_user.role != 'admin':
                    return jsonify(code=403,msg='此操作需要管理员权限'),403
                return function(*args,**kwargs)
            return authenticated

    @app.before_request
    def same_origin():
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('Origin')
            if (origin and urlsplit(origin).netloc != request.host) or request.headers.get('Sec-Fetch-Site')=='cross-site':
                return jsonify(code=403,msg='不允许跨站操作'),403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify(code=400,msg=str(error)),400

    @app.errorhandler(LookupError)
    def not_found(error):
        return jsonify(code=404,msg=str(error)),404

    @app.errorhandler(sqlite3.IntegrityError)
    def duplicate(error):
        return jsonify(code=409,msg='工号已存在，或任务已在队列中'),409

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(code=413,msg='请求内容过大'),413

    @app.errorhandler(EnrollmentError)
    def enrollment_error(error):
        return jsonify(code=error.status,msg=str(error)),error.status

    @app.post('/api/daily-practice/public/enroll')
    def public_enroll():
        user,password = parse_submission(request.get_json(silent=True))
        address = request.remote_addr or 'unknown'
        if address in ('127.0.0.1','::1'):
            address = request.headers.get('X-Real-IP',address)[:64]
        if not enrollment_limiter.allow(address,user):
            return jsonify(code=429,msg='提交较频繁，请 15 分钟后重试'),429,{'Retry-After':'900'}
        if not enrollment_limiter.gate.acquire(blocking=False):
            return jsonify(code=429,msg='门户验证繁忙，请稍后重试'),429,{'Retry-After':'10'}
        try:
            profile = verify_portal_credentials(user,password)
            name = profile.get('employee_name',user) if isinstance(profile,dict) else user
            account = store.save({'employee_id':user,'employee_name':name,'password':password},public_enrollment=True)
        finally:
            enrollment_limiter.gate.release()
        return jsonify(code=200,msg='接入成功，将于每天北京时间 16:00 仅查询进度',data={
            'execution_mode':'check','run_time':'16:00','timezone':'Asia/Shanghai','next_run':account['next_run'],
        })

    @app.get('/health')
    def health():
        return jsonify(status='ok',scheduler=bool(worker.thread and worker.thread.is_alive()))

    @app.get('/api/daily-practice/overview')
    @authenticator
    def overview():
        accounts = store.accounts()
        return jsonify(code=200,data=dict(accounts=accounts,date=now().date().isoformat(),
                                          timezone='Asia/Shanghai',scheduler=bool(worker.thread and worker.thread.is_alive() and os.environ.get('DAILY_MANAGER_PAUSED')!='1')))

    @app.post('/api/daily-practice/accounts')
    @authenticator
    def create_account():
        return jsonify(code=200,data=store.save(request.get_json(silent=True)),msg='人员已添加')

    @app.put('/api/daily-practice/accounts/<int:account_id>')
    @authenticator
    def update_account(account_id):
        return jsonify(code=200,data=store.save(request.get_json(silent=True),account_id),msg='人员信息已保存')

    @app.delete('/api/daily-practice/accounts/<int:account_id>')
    @authenticator
    def delete_account(account_id):
        store.delete(account_id)
        return jsonify(code=200,data=None,msg='人员已移除，历史记录已保留')

    @app.post('/api/daily-practice/accounts/<int:account_id>/run')
    @authenticator
    def run_account(account_id):
        body = request.get_json(silent=True)
        if not isinstance(body,dict) or set(body)-{'mode'}:
            raise ValueError('任务请求格式不正确')
        job = store.enqueue(account_id,mode=body.get('mode'))
        if not job:
            return jsonify(code=409,msg='该人员已有正在执行或排队的任务'),409
        return jsonify(code=200,data={'job_id':job},msg='任务已加入队列')

    @app.get('/api/daily-practice/history')
    @authenticator
    def history():
        value = request.args.get('account_id')
        if value is not None and not value.isdigit():
            raise ValueError('人员编号不正确')
        return jsonify(code=200,data=store.history(int(value) if value else None))

    if start_worker:
        worker.start()
    return app


if os.environ.get('DAILY_MANAGER_WSGI')=='1':
    app = create_app(start_worker=True)
