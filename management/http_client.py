"""Read completion directly through the portal's verified HTTP flow."""
import requests
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

import config
from auth import login
from exceptions import LoginError, NetworkError


def completion_value(result):
    if not isinstance(result,dict) or result.get('code') != 200 or type(result.get('data')) is not bool:
        raise ValueError('Completion response was not authoritative')
    return result['data']


class PortalClient:
    def __init__(self, employee_id=None, password=None, *, session=None, progress=None,
                 cache_path=None, credential_version=None):
        self.employee_id = config.EMPLOYEE_ID if employee_id is None else employee_id
        self.password = config.PASSWORD if password is None else password
        self.session = session if session is not None else requests.Session()
        self.progress = progress or (lambda stage:None)
        self.credential_version = credential_version or os.environ.get('DAILY_PRACTICE_CREDENTIAL_VERSION')
        self.cache_path = Path(cache_path) if cache_path is not None else config.STATE_DIR/'http_session.json'
        self.scope = hashlib.sha256('\0'.join((config.SESSION_API_URL,config.COMPLETION_API_URL,config.APP_ID_STR)).encode()).hexdigest()
        self.used_cached_token = False
        self.token = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    @staticmethod
    def valid_token(token):
        return isinstance(token,str) and bool(token.strip()) and len(token)<=16384 and not any(ord(char)<32 for char in token)

    @staticmethod
    def expiry_hint(token):
        # This only guides cache freshness. The portal still validates the token.
        try:
            payload = token.split('.')[1]
            value = json.loads(base64.urlsafe_b64decode(payload+'='*((-len(payload))%4))).get('exp')
            if type(value) in (int,float) and math.isfinite(value):
                return value
        except (ValueError,IndexError,TypeError,AttributeError):
            pass
        return None

    def load_token(self):
        if not self.credential_version:
            return None
        try:
            descriptor = os.open(self.cache_path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(descriptor,'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600:
                    return None
                raw = stream.read(65537)
            if len(raw)>65536:
                return None
            cached = json.loads(raw)
            if not isinstance(cached,dict) or cached.get('employee_id')!=self.employee_id or cached.get('credential_version')!=self.credential_version or cached.get('scope')!=self.scope:
                return None
            token = cached.get('token')
            if not self.valid_token(token):
                return None
            expires = self.expiry_hint(token)
            if expires is None:
                saved_at = cached.get('saved_at')
                if type(saved_at) not in (int,float) or not math.isfinite(saved_at):
                    return None
                expires = saved_at+86400
            return token if expires>time.time()+60 else None
        except (OSError,ValueError,TypeError):
            return None

    def save_token(self, token):
        if not self.credential_version:
            return
        temporary = None
        try:
            self.cache_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            descriptor,temporary = tempfile.mkstemp(prefix='.http-session-',dir=self.cache_path.parent)
            with os.fdopen(descriptor,'w') as stream:
                json.dump(dict(employee_id=self.employee_id,credential_version=self.credential_version,
                               scope=self.scope,token=token,saved_at=time.time()),stream)
            os.replace(temporary,self.cache_path)
        except OSError:
            # Cache failure must not turn a successful portal query into failure.
            pass
        finally:
            if temporary:
                try:os.unlink(temporary)
                except OSError:pass

    def clear_token(self):
        if self.credential_version:
            try:self.cache_path.unlink()
            except OSError:pass

    def post(self, endpoint, *, retry_connect=True, **kwargs):
        attempts = 2 if retry_connect else 1
        for attempt in range(attempts):
            try:
                response = self.session.post(endpoint,timeout=15,allow_redirects=False,**kwargs)
                break
            except requests.ConnectTimeout:
                if attempt==attempts-1:
                    raise NetworkError('Portal connection timed out') from None
            except requests.RequestException:
                raise NetworkError('Portal request failed') from None
        if response.status_code in (401,403):
            raise LoginError('Portal session rejected')
        if response.status_code==429 or response.status_code>=500:
            raise NetworkError('Portal service unavailable')
        if response.status_code != 200:
            raise ValueError('Unexpected portal HTTP response')
        try:
            result = response.json()
        except ValueError:
            raise ValueError('Invalid portal JSON response') from None
        if not isinstance(result,dict):
            raise ValueError('Invalid portal response structure')
        return result

    def authorized_post(self, endpoint, payload=None):
        if not self.token:
            raise LoginError('No verified portal session')
        kwargs = {'headers':{'Authorization':self.token}}
        if payload is not None:
            kwargs['json'] = payload
        return self.post(endpoint,retry_connect=False,**kwargs)

    def query(self, token):
        self.progress('checking')
        result = self.post(config.COMPLETION_API_URL,headers={'Authorization':token})
        if result.get('code') in (401,403):
            raise LoginError('Completion session rejected')
        return completion_value(result)

    def completed(self):
        self.used_cached_token = False
        if not all((self.employee_id,self.password,config.LOGIN_URL,config.SESSION_API_URL,
                    config.APP_ID_STR,config.COMPLETION_API_URL)):
            raise ValueError('Portal HTTP configuration is incomplete')
        cached = self.load_token()
        if cached:
            self.token = cached
            try:
                result = self.query(cached)
                self.used_cached_token = True
                return result
            except LoginError:
                self.clear_token()
                self.token = None
        self.progress('authenticating')
        identity = login(self.employee_id,self.password,session=self.session)
        if str(identity.get('userName')) != self.employee_id:
            raise LoginError('Portal identity mismatch')
        session = self.post(config.SESSION_API_URL,
                            params={'userName':self.employee_id,'appId':config.APP_ID_STR})
        token = session.get('data')
        if session.get('status') != 1 or not self.valid_token(token):
            raise LoginError('Portal did not provide a session')
        self.save_token(token)
        self.token = token
        try:
            return self.query(token)
        except LoginError:
            self.clear_token()
            raise
