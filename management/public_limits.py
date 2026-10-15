"""Persistent sliding-window limits shared by anonymous daily-practice APIs."""
from ipaddress import ip_address
import math
import time


def client_address(request):
    def parse(value):
        address = ip_address(value)
        return getattr(address,'ipv4_mapped',None) or address

    try:
        peer = parse(request.remote_addr or '')
    except ValueError:
        return 'unknown'
    # The loopback-only backend is reached through an SSH tunnel. The edge
    # proxy MUST overwrite X-Real-IP with its socket peer, never a client header.
    if peer.is_loopback:
        try:
            return str(parse(request.headers.get('X-Real-IP','')))
        except ValueError:
            pass
    return str(peer)


class PublicIPLimiter:
    MINUTE_LIMIT = 5
    HOUR_LIMIT = 20
    BAN_SECONDS = 24*60*60

    def __init__(self,store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS public_ip_requests (ip TEXT NOT NULL,at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS public_ip_window ON public_ip_requests(ip,at);
              CREATE INDEX IF NOT EXISTS public_ip_expiry ON public_ip_requests(at);
              CREATE TABLE IF NOT EXISTS public_ip_bans (ip TEXT PRIMARY KEY,until REAL NOT NULL);
            ''')

    def consume(self,address):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            at = time.time()
            db.execute('DELETE FROM public_ip_requests WHERE at<=?',(at-3600,))
            db.execute('DELETE FROM public_ip_bans WHERE until<=?',(at,))
            ban = db.execute('SELECT until FROM public_ip_bans WHERE ip=?',(address,)).fetchone()
            if ban:
                return max(1,math.ceil(ban['until']-at))
            counts = db.execute('''SELECT count(*) AS hour_count,
              coalesce(sum(at>?),0) AS minute_count FROM public_ip_requests WHERE ip=?''',
              (at-60,address)).fetchone()
            if counts['minute_count']>=self.MINUTE_LIMIT or counts['hour_count']>=self.HOUR_LIMIT:
                db.execute('INSERT INTO public_ip_bans(ip,until) VALUES(?,?)',(address,at+self.BAN_SECONDS))
                db.execute('DELETE FROM public_ip_requests WHERE ip=?',(address,))
                return self.BAN_SECONDS
            db.execute('INSERT INTO public_ip_requests(ip,at) VALUES(?,?)',(address,at))
        return 0
