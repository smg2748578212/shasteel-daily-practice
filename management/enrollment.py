"""Credential verification and bounded throttling for public enrollment."""
import hashlib
import re
import threading
import time
from collections import OrderedDict

from auth import login
from exceptions import LoginError, NetworkError


class EnrollmentError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def parse_submission(data):
    if not isinstance(data, dict) or set(data) != {'user', 'password'}:
        raise ValueError('请仅提交用户和密码')
    user, password = data['user'], data['password']
    if not isinstance(user, str) or not re.fullmatch(r'[0-9]{1,20}', user):
        raise ValueError('用户需为 1–20 位数字工号')
    if not isinstance(password, str) or not password or len(password) > 256 or '\0' in password:
        raise ValueError('请填写有效的门户密码')
    return user, password


def verify_portal_credentials(user, password):
    try:
        result = login(user, password)
        if str(result.get('userName')) != user:
            raise LoginError('Identity mismatch')
        name = result.get('chineseName')
        name = name.strip() if isinstance(name,str) else ''
        if not name or len(name)>80 or any(ord(c)<32 for c in name):
            name = user
        return {'employee_name':name}
    except LoginError:
        raise EnrollmentError('用户或密码不正确，请核对门户登录信息', 401) from None
    except NetworkError:
        raise EnrollmentError('门户暂时无法连接，请稍后重试', 503) from None
    except Exception:
        raise EnrollmentError('门户验证暂时不可用，请稍后重试', 503) from None


class EnrollmentLimiter:
    """One-worker deployment: at most two concurrent portal validations."""
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.gate = threading.BoundedSemaphore(2)

    def allow(self, address, user):
        at = time.monotonic()
        keys = [('global', 300), ('ip:' + address, 60),
                ('user:' + hashlib.sha256(user.encode()).hexdigest(), 5)]
        with self.lock:
            for key, limit in keys:
                start, count = self.entries.get(key, (at, 0))
                if at - start < 900 and count >= limit:
                    return False
            for key, _ in keys:
                start, count = self.entries.get(key, (at, 0))
                self.entries[key] = (start, count + 1) if at - start < 900 else (at, 1)
                self.entries.move_to_end(key)
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)
            return True
