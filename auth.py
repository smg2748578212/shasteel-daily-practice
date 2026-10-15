import hashlib
import base64
import requests
import config
from exceptions import LoginError, TokenExpiredError, NetworkError

def encode_password(password: str) -> str:
    pass
    raw_md5 = hashlib.md5(password.encode()).digest()
    return base64.b64encode(raw_md5).decode()

def compute_auth_token(user_unique_token: str) -> str:
    pass
    return hashlib.sha256((user_unique_token + 'shasteel').encode()).hexdigest()

def login(employee_id=None, password=None) -> dict:
    pass
    employee_id = config.EMPLOYEE_ID if employee_id is None else employee_id
    password = config.PASSWORD if password is None else password
    encoded_pw = encode_password(password)
    payload = {'userName': employee_id, 'password': encoded_pw}
    try:
        resp = requests.post(config.LOGIN_URL, json=payload, timeout=15)
    except requests.RequestException as e:
        raise NetworkError(f'无法连接登录接口: {e}')
    if resp.status_code != 200:
        raise LoginError(f'登录接口返回 HTTP {resp.status_code}')
    data = resp.json()
    if data.get('status') != 1:
        raise LoginError(f"登录失败: {data.get('msg', '未知错误')}")
    user_data = data['data']
    if not user_data.get('userUniqueToken'):
        raise LoginError('登录响应中缺少 userUniqueToken')
    return user_data

def get_sso_redirect(user_unique_token: str) -> dict:
    pass
    auth_token = compute_auth_token(user_unique_token)
    headers = {'userToken': auth_token, 'userName': config.EMPLOYEE_ID, 'Content-Type': 'application/json'}
    try:
        resp = requests.post(f'{config.SSO_REDIRECT_URL}?appId={config.APP_ID}', headers=headers, timeout=15)
    except requests.RequestException as e:
        raise NetworkError(f'无法连接 SSO 接口: {e}')
    if resp.status_code == 401:
        raise TokenExpiredError('Token 已过期')
    if resp.status_code != 200:
        raise LoginError(f'SSO 重定向返回 HTTP {resp.status_code}')
    data = resp.json()
    if data.get('status') != 1:
        raise LoginError(f"SSO 重定向失败: {data.get('msg', '未知错误')}")
    return data['data']

def build_daily_practice_url(app_access_token: str) -> str:
    pass
    return f'{config.H5_BASE}/#/login?userName={config.EMPLOYEE_ID}&appId={config.APP_ID_STR}&userNumber={config.EMPLOYEE_ID}&token={app_access_token}&appId={config.APP_ID_STR}'

def authenticate() -> tuple:
    pass
    user_data = login()
    if str(user_data.get('userName')) != config.EMPLOYEE_ID:
        raise LoginError('登录响应的账号不匹配')
    sso_data = get_sso_redirect(user_data['userUniqueToken'])
    practice_url = build_daily_practice_url(sso_data['appAccessToken'])
    return (user_data, sso_data, practice_url)

def save_token_cache(app_access_token: str):
    pass
    import json
    try:
        config.TOKEN_CACHE_FILE.write_text(json.dumps({'employee_id': config.EMPLOYEE_ID, 'appAccessToken': app_access_token}))
        config.TOKEN_CACHE_FILE.chmod(0o600)
    except Exception:
        pass

def load_token_cache() -> str | None:
    pass
    import json
    try:
        if config.TOKEN_CACHE_FILE.exists():
            data = json.loads(config.TOKEN_CACHE_FILE.read_text())
            owner = data.get('employee_id')
            if owner and str(owner) != config.EMPLOYEE_ID:
                return None
            if not owner and config.STATE_DIR != config.BASE_DIR:
                return None
            return data.get('appAccessToken')
    except Exception:
        pass
    return None

def try_direct_access(page) -> bool:
    pass
    token = load_token_cache()
    if not token:
        return False
    url = build_daily_practice_url(token)
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=config.BROWSER_LONG_TIMEOUT)
    except Exception:
        return False
    try:
        page.wait_for_load_state('networkidle', timeout=config.BROWSER_LONG_TIMEOUT)
    except Exception:
        pass
    content = page.content()
    if any((kw in content for kw in ['每日一练', '开始答题', '已完成', '查看解析'])):
        return True
    return False
