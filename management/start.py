"""Load protected deployment settings and start the single scheduler worker."""
import json
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    os.umask(0o077)
    path = Path(os.environ.get('DAILY_MANAGER_CONFIG','/var/lib/daily-practice/runtime.json'))
    values = json.loads(path.read_text())
    if not isinstance(values,dict) or any(not isinstance(v,str) for v in values.values()):
        raise ValueError('Runtime settings must be a string mapping')
    os.environ.update(values)
    os.environ['DAILY_MANAGER_WSGI'] = '1'
    os.environ['TZ'] = 'Asia/Shanghai'
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    os.execv(sys.executable,[sys.executable,'-m','gunicorn','management.app:app','--bind','127.0.0.1:'+os.environ.get('DAILY_MANAGER_PORT','5011'),
                         '--workers','1','--threads','4','--timeout','90','--access-logfile','-','--error-logfile','-'])
