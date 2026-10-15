from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flask import jsonify,request

from management.app import create_app
from management.store import Store,TZ,next_execution


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.person = self.store.save(dict(employee_id='00000001',employee_name='Example',password='synthetic-secret',run_time='07:15'))

    def tearDown(self):
        self.temp.cleanup()

    def test_credentials_are_encrypted_and_never_returned(self):
        raw = Path(self.store.path).read_bytes()
        self.assertNotIn(b'synthetic-secret',raw)
        public = json.dumps(self.store.accounts())
        self.assertNotIn('synthetic-secret',public)
        self.assertNotIn('"secret"',public)
        self.store.save({'team':'Example team','password':''},self.person['id'])
        self.assertEqual(self.store.credentials(self.person['id'])[2],'synthetic-secret')

    def test_same_person_cannot_overlap_or_repeat_a_schedule(self):
        at = datetime(2026,10,15,8,0,tzinfo=TZ)
        self.store.schedule(at)
        self.store.schedule(at)
        self.assertEqual(len(self.store.history()),1)
        self.assertIsNone(self.store.enqueue(self.person['id'],'check'))
        job = self.store.claim()
        self.store.finish(job['id'],completed=True)
        self.store.schedule(at)
        self.assertEqual(len(self.store.history()),1)

    def test_paused_accounts_never_enqueue(self):
        self.store.save({'enabled':False},self.person['id'])
        with self.assertRaises(ValueError):self.store.enqueue(self.person['id'])
        self.store.schedule(datetime(2026,10,15,8,0,tzinfo=TZ))
        self.assertEqual(self.store.history(),[])

    def test_pausing_cancels_queue_without_interrupting_running_jobs(self):
        job = self.store.enqueue(self.person['id'])
        self.store.save({'enabled':False},self.person['id'])
        self.assertEqual(self.store.history()[0]['status'],'interrupted')
        self.store.save({'enabled':True},self.person['id'])
        self.store.enqueue(self.person['id'])
        self.store.claim()
        self.store.save({'enabled':False},self.person['id'])
        self.assertEqual(self.store.history()[0]['status'],'running')

    def test_failure_and_restart_do_not_mark_completion(self):
        job = self.store.enqueue(self.person['id'])
        self.store.claim()
        self.store.recover()
        self.assertEqual(self.store.history()[0]['status'],'interrupted')
        self.assertIsNone(self.store.accounts()[0]['completion'])
        second = self.store.enqueue(self.person['id'])
        self.store.claim()
        self.store.finish(second,error='Synthetic failure')
        self.assertEqual(self.store.history()[0]['status'],'failed')
        self.assertIsNone(self.store.accounts()[0]['completion'])

    def test_weekday_and_timezone_schedule(self):
        person = self.store.save({'run_days':[0]},self.person['id'])
        at = datetime(2026,10,15,8,0,tzinfo=TZ)
        expected = datetime(2026,10,19,7,15,tzinfo=TZ).isoformat(timespec='seconds')
        self.assertEqual(next_execution(person,at),expected)
        self.store.schedule(at)
        self.assertEqual(self.store.history(),[])

    def test_validation_does_not_replace_existing_password(self):
        for change in [{'run_time':'24:00'},{'run_days':[True]},{'employee_id':'changed'},{'run_days':[]}]:
            with self.assertRaises(ValueError):self.store.save(change,self.person['id'])
        self.assertEqual(self.store.credentials(self.person['id'])[2],'synthetic-secret')


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        def authenticate(function):
            def protected(*args,**kwargs):
                if request.headers.get('X-Test-Role')!='admin':
                    return jsonify(code=403,msg='Administrator required'),403
                return function(*args,**kwargs)
            protected.__name__=function.__name__
            return protected
        self.app = create_app(self.temp.name,authenticate)
        self.client = self.app.test_client()
        self.headers = {'X-Test-Role':'admin'}

    def tearDown(self):self.temp.cleanup()

    def test_unauthorized_and_cross_site_requests_cannot_maintain_people(self):
        self.assertEqual(self.client.get('/api/daily-practice/overview').status_code,403)
        response = self.client.post('/api/daily-practice/accounts',json={},headers={**self.headers,'Origin':'https://external.example'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.app.extensions['daily_store'].accounts(),[])

    def test_create_edit_queue_and_archive_contract(self):
        response = self.client.post('/api/daily-practice/accounts',json={
            'employee_id':'00000001','employee_name':'Example','password':'synthetic-secret',
        },headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('synthetic-secret',response.get_data(as_text=True))
        account = response.json['data']['id']
        path = '/api/daily-practice/accounts/'+str(account)
        job = self.client.post(path+'/run',json={'mode':'check'},headers=self.headers)
        self.assertEqual(job.status_code,200)
        self.assertEqual(self.client.post(path+'/run',json={},headers=self.headers).status_code,409)
        self.assertEqual(self.client.delete(path,headers=self.headers).status_code,400)
        store = self.app.extensions['daily_store'];store.claim();store.finish(job.json['data']['job_id'],completed=False)
        self.assertEqual(self.client.delete(path,headers=self.headers).status_code,200)
        self.assertEqual(store.accounts(),[])
        self.assertEqual(len(store.history()),1)


if __name__=='__main__':unittest.main()
