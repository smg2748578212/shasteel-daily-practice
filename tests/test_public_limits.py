from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask,jsonify,request

from management.app import create_app
from management.public_limits import PublicIPLimiter,client_address
from management.store import Store


class PublicLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.limiter = PublicIPLimiter(self.store)
        self.clock = patch('management.public_limits.time.time',return_value=10000)
        self.time = self.clock.start();self.addCleanup(self.clock.stop)

    def test_sixth_request_bans_for_exactly_24_hours_without_extending(self):
        for _ in range(5):self.assertEqual(self.limiter.consume('192.0.2.1'),0)
        self.assertEqual(self.limiter.consume('192.0.2.1'),86400)
        self.time.return_value += 123.2
        self.assertEqual(self.limiter.consume('192.0.2.1'),86277)
        self.assertEqual(self.limiter.consume('192.0.2.2'),0)
        self.time.return_value = 10000+86400
        for _ in range(5):self.assertEqual(self.limiter.consume('192.0.2.1'),0)
        self.assertEqual(self.limiter.consume('192.0.2.1'),86400)

    def test_hourly_21st_request_bans_even_when_minute_quota_is_clear(self):
        for offset in [0,61,122,183]:
            self.time.return_value = 10000+offset
            for _ in range(5):self.assertEqual(self.limiter.consume('192.0.2.1'),0)
        self.time.return_value = 10000+244
        self.assertEqual(self.limiter.consume('192.0.2.1'),86400)

    def test_rolling_windows_release_at_exact_boundary_and_clean_old_records(self):
        for offset in [0,60,120,180]:
            self.time.return_value = 10000+offset
            for _ in range(5):self.assertEqual(self.limiter.consume('192.0.2.1'),0)
        self.time.return_value = 13600
        for _ in range(5):self.assertEqual(self.limiter.consume('192.0.2.1'),0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM public_ip_requests').fetchone()[0],20)

    def test_counts_and_bans_survive_recreating_app_store_and_limiter(self):
        for _ in range(4):self.limiter.consume('192.0.2.1')
        reloaded = PublicIPLimiter(Store(self.temp.name))
        self.assertEqual(reloaded.consume('192.0.2.1'),0)
        self.assertEqual(reloaded.consume('192.0.2.1'),86400)
        self.assertEqual(PublicIPLimiter(Store(self.temp.name)).consume('192.0.2.1'),86400)

    def test_concurrent_requests_share_one_transactional_quota(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:self.limiter.consume('192.0.2.1'),range(8)))
        self.assertEqual(results.count(0),5)
        self.assertEqual(results.count(86400),3)

    def test_only_loopback_proxy_can_supply_real_ip_and_ip_forms_are_canonical(self):
        app = Flask(__name__)
        for remote,header,expected in [
            ('192.0.2.1','192.0.2.2','192.0.2.1'),
            ('127.0.0.1','192.0.2.2','192.0.2.2'),
            ('::1','2001:0db8::1','2001:db8::1'),
            ('127.0.0.1','::ffff:192.0.2.2','192.0.2.2'),
            ('127.0.0.1','192.0.2.1,192.0.2.2','127.0.0.1'),
        ]:
            with app.test_request_context(environ_base={'REMOTE_ADDR':remote},headers={'X-Real-IP':header,'X-Forwarded-For':'192.0.2.3'}):
                self.assertEqual(client_address(request),expected)


class PublicLimitAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        def deny(function):
            def protected(*args,**kwargs):return jsonify(code=403),403
            protected.__name__ = function.__name__;return protected
        self.authenticator = deny
        self.app = create_app(self.temp.name,deny)
        self.client = self.app.test_client()
        self.clock = patch('management.public_limits.time.time',return_value=10000)
        self.time = self.clock.start();self.addCleanup(self.clock.stop)

    def test_both_public_endpoints_share_quota_before_validation_or_portal_login(self):
        for _ in range(3):self.assertEqual(self.client.get('/api/daily-practice/public/roster').status_code,200)
        for _ in range(2):self.assertEqual(self.client.post('/api/daily-practice/public/enroll',json={}).status_code,400)
        with patch('management.app.verify_portal_credentials') as verify:
            response = self.client.post('/api/daily-practice/public/enroll',json={'user':'00000001','password':'synthetic'})
            self.assertEqual(response.status_code,429);verify.assert_not_called()
        self.assertEqual(response.headers['Retry-After'],'86400')
        self.assertEqual(response.json['data']['retry_after'],86400)
        self.assertEqual(self.client.get('/api/daily-practice/public/roster?date=2026-10-15').status_code,429)
        self.assertEqual(self.client.get('/api/daily-practice/overview').status_code,403)
        self.assertEqual(self.client.get('/health').status_code,200)
        self.assertEqual(self.app.extensions['daily_store'].accounts(),[])

    def test_spoofing_forwarding_headers_does_not_reset_quota(self):
        for number in range(5):
            response = self.client.get('/api/daily-practice/public/roster',environ_overrides={'REMOTE_ADDR':'192.0.2.10'},
                                       headers={'X-Real-IP':f'192.0.2.{number+20}','X-Forwarded-For':f'192.0.2.{number+30}'})
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/daily-practice/public/roster',environ_overrides={'REMOTE_ADDR':'192.0.2.10'},headers={'X-Real-IP':'192.0.2.99'}).status_code,429)
        self.assertEqual(self.client.get('/api/daily-practice/public/roster',environ_overrides={'REMOTE_ADDR':'192.0.2.11'}).status_code,200)

    def test_proxy_separates_real_visitors_and_banned_ip_remains_blocked_after_app_restart(self):
        for _ in range(6):
            response = self.client.get('/api/daily-practice/public/roster',headers={'X-Real-IP':'192.0.2.10'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(self.client.get('/api/daily-practice/public/roster',headers={'X-Real-IP':'192.0.2.11'}).status_code,200)
        self.client = create_app(self.temp.name,self.authenticator).test_client()
        self.time.return_value += 100
        response = self.client.get('/api/daily-practice/public/roster',headers={'X-Real-IP':'192.0.2.10'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['Retry-After'],'86300')


if __name__=='__main__':unittest.main()
