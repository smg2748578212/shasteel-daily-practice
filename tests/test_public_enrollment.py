"""Public enrollment tests use synthetic credentials and never contact the portal."""
from datetime import datetime, timedelta
import tempfile
import unittest
from unittest.mock import Mock, patch

from flask import jsonify

from management.app import create_app
from management.enrollment import EnrollmentError, EnrollmentLimiter, verify_portal_credentials
from management.store import TZ
from exceptions import LoginError, NetworkError


class PublicEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        def protect(function):
            def denied(*args, **kwargs):
                return jsonify(code=403), 403
            denied.__name__ = function.__name__
            return denied
        self.app = create_app(self.temp.name, protect)
        self.client = self.app.test_client()
        self.store = self.app.extensions['daily_store']
        self.body = {'user':'00000002','password':'synthetic-public-secret'}
        self.at = datetime(2026,10,15,15,0,tzinfo=TZ)
        self.clock = patch('management.store.now',return_value=self.at);self.clock.start()
        self.verify = patch('management.app.verify_portal_credentials');self.verifier = self.verify.start()
        self.addCleanup(self.clock.stop);self.addCleanup(self.verify.stop);self.addCleanup(self.temp.cleanup)

    def submit(self, body=None, **kwargs):
        return self.client.post('/api/daily-practice/public/enroll',json=self.body if body is None else body,**kwargs)

    def test_anonymous_enrollment_only_exposes_fixed_schedule(self):
        response = self.submit();self.assertEqual(response.status_code,200)
        self.verifier.assert_called_once_with(self.body['user'],self.body['password'])
        self.assertEqual(set(response.json['data']),{'execution_mode','run_time','timezone','next_run'})
        self.assertEqual(response.json['data']['next_run'],'2026-10-15T16:00:00+08:00')
        person = self.store.accounts()[0]
        self.assertEqual((person['enrollment_source'],person['execution_mode'],person['run_time']),('public','check','16:00'))
        self.assertEqual(person['run_days'],list(range(7)))
        self.assertTrue(person['enabled']);self.assertEqual(person['employee_name'],self.body['user'])
        self.assertNotIn(self.body['password'],response.get_data(as_text=True))
        self.assertNotIn(self.body['user'],response.get_data(as_text=True))
        self.assertEqual(self.store.history(),[])
        with self.assertRaisesRegex(ValueError,'仅查看进度'):self.store.enqueue(person['id'],'run')

    def test_verified_portal_name_is_saved_without_a_third_input(self):
        self.verifier.return_value={'employee_name':'测试姓名'}
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(self.store.accounts()[0]['employee_name'],'测试姓名')

    def test_invalid_password_never_changes_existing_person(self):
        person=self.store.save({'employee_id':self.body['user'],'employee_name':'Existing','password':'original-secret','execution_mode':'run'})
        before=self.store.accounts()
        self.verifier.side_effect=EnrollmentError('用户或密码不正确，请核对门户登录信息',401)
        response=self.submit();self.assertEqual(response.status_code,401)
        self.assertEqual(self.store.accounts(),before)
        self.assertEqual(self.store.credentials(person['id'])[2],'original-secret')

    def test_repeat_updates_password_and_converts_existing_queue_without_duplicates(self):
        person=self.store.save({'employee_id':self.body['user'],'employee_name':'Existing','team':'Example','password':'original-secret','execution_mode':'run'})
        job=self.store.enqueue(person['id'],'run')
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(self.submit().status_code,200)
        people=self.store.accounts();self.assertEqual(len(people),1)
        self.assertEqual((people[0]['employee_name'],people[0]['team']),('Existing','Example'))
        self.assertEqual(self.store.credentials(person['id'])[2],self.body['password'])
        self.assertEqual(self.store.claim()['mode'],'check')
        self.assertEqual(self.store.history()[0]['id'],job)

    def test_running_answering_and_archived_accounts_are_not_overwritten(self):
        person=self.store.save({'employee_id':self.body['user'],'employee_name':'Existing','password':'original-secret','execution_mode':'run'})
        job=self.store.enqueue(person['id'],'run');self.store.claim()
        self.assertEqual(self.submit().status_code,400)
        self.assertEqual(self.store.accounts()[0]['enrollment_source'],'admin')
        self.store.finish(job,completed=False);self.store.delete(person['id'])
        self.assertEqual(self.submit().status_code,400)
        self.assertEqual(self.store.accounts(),[])

    def test_admin_cannot_upgrade_public_users_or_change_the_daily_schedule(self):
        self.submit();person=self.store.accounts()[0]
        for change in [{'execution_mode':'run'},{'run_time':'07:00'},{'run_days':[0]},{'enrollment_source':'admin'}]:
            with self.assertRaises(ValueError):self.store.save(change,person['id'])
        self.store.save({'team':'Example','enabled':False},person['id'])
        self.assertFalse(self.store.accounts()[0]['enabled'])
        self.assertEqual(self.submit().status_code,200)
        self.assertTrue(self.store.accounts()[0]['enabled'])

    def test_one_check_per_day_at_16_and_no_completion_submission(self):
        self.submit()
        self.store.schedule(self.at.replace(hour=15,minute=59));self.assertEqual(self.store.history(),[])
        due=self.at.replace(hour=16)
        self.store.schedule(due);self.store.schedule(due)
        self.assertEqual(len(self.store.history()),1)
        job=self.store.claim();self.assertEqual(job['mode'],'check');self.store.finish(job['id'],completed=False)
        self.store.schedule(due.replace(hour=17));self.assertEqual(len(self.store.history()),1)
        self.store.schedule(due+timedelta(days=1));self.assertEqual(len(self.store.history()),2)
        self.assertTrue(all(j['mode']=='check' for j in self.store.history()))

    def test_enrollment_after_16_starts_next_day(self):
        with patch('management.store.now',return_value=self.at.replace(hour=17)):
            response=self.submit()
            self.assertEqual(response.json['data']['next_run'],'2026-10-16T16:00:00+08:00')
            self.store.schedule(self.at.replace(hour=17));self.assertEqual(self.store.history(),[])
            self.store.schedule(self.at.replace(hour=16)+timedelta(days=1))
            self.assertEqual(len(self.store.history()),1)

    def test_public_route_does_not_unlock_admin_data_or_mutation(self):
        self.submit();person=self.store.accounts()[0]
        for path in ['/api/daily-practice/overview','/api/daily-practice/history']:
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.put('/api/daily-practice/accounts/'+str(person['id']),json={'execution_mode':'run'}).status_code,403)
        self.assertEqual(self.client.post('/api/daily-practice/accounts',json={}).status_code,403)

    def test_extra_fields_and_invalid_inputs_are_rejected_before_portal_login(self):
        for index,body in enumerate([{},[],{**self.body,'execution_mode':'run'},{**self.body,'run_time':'07:00'},
                     {'user':'abc','password':'secret'},{'user':'00000002','password':''},
                     {'user':'00000002','password':None}]):
            self.assertEqual(self.submit(body,environ_overrides={'REMOTE_ADDR':f'192.0.2.{index+1}'}).status_code,400)
        self.verifier.assert_not_called();self.assertEqual(self.store.accounts(),[])

    def test_cross_site_enrollment_is_rejected(self):
        self.assertEqual(self.submit(headers={'Origin':'https://external.example'}).status_code,403)
        self.verifier.assert_not_called()

    def test_account_throttling_is_bounded_and_returns_retry_after(self):
        self.verifier.side_effect=EnrollmentError('Invalid credentials',401)
        for _ in range(5):self.assertEqual(self.submit().status_code,401)
        response=self.submit();self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['Retry-After'],'86400');self.assertEqual(self.verifier.call_count,5)
        self.assertEqual(self.store.accounts(),[])


class PortalValidationTests(unittest.TestCase):
    def test_verifier_rejects_wrong_identity_and_sanitizes_portal_errors(self):
        with patch('management.enrollment.login',return_value={'userName':'00000002','chineseName':'测试姓名'}):
            self.assertEqual(verify_portal_credentials('00000002','synthetic-secret'),{'employee_name':'测试姓名'})
        for error,status in [(LoginError('private upstream detail'),401),(NetworkError('private upstream detail'),503),(ValueError('private upstream detail'),503)]:
            with patch('management.enrollment.login',side_effect=error):
                with self.assertRaises(EnrollmentError) as raised:verify_portal_credentials('00000002','synthetic-secret')
                self.assertEqual(raised.exception.status,status);self.assertNotIn('private',str(raised.exception))
        with patch('management.enrollment.login',return_value={'userName':'00000003'}):
            with self.assertRaises(EnrollmentError) as raised:verify_portal_credentials('00000002','synthetic-secret')
            self.assertEqual(raised.exception.status,401)

    def test_portal_login_uses_request_credentials_without_mutating_global_config(self):
        import auth
        response=Mock(status_code=200);response.json.return_value={'status':1,'data':{'userUniqueToken':'synthetic-token'}}
        before=(auth.config.EMPLOYEE_ID,auth.config.PASSWORD)
        with patch.object(auth.requests,'post',return_value=response) as post:
            auth.login('00000002','synthetic-secret')
        self.assertEqual(post.call_args.kwargs['json'],{'userName':'00000002','password':auth.encode_password('synthetic-secret')})
        self.assertEqual((auth.config.EMPLOYEE_ID,auth.config.PASSWORD),before)

    def test_limiter_releases_window_and_bounds_concurrent_validation(self):
        limiter=EnrollmentLimiter()
        with patch('management.enrollment.time.monotonic',return_value=0):
            for _ in range(5):self.assertTrue(limiter.allow('example','00000002'))
            self.assertFalse(limiter.allow('example','00000002'))
        with patch('management.enrollment.time.monotonic',return_value=901):
            self.assertTrue(limiter.allow('example','00000002'))
        self.assertTrue(limiter.gate.acquire(False));self.assertTrue(limiter.gate.acquire(False))
        self.assertFalse(limiter.gate.acquire(False));limiter.gate.release();limiter.gate.release()


if __name__=='__main__':unittest.main()
