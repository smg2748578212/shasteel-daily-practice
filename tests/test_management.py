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
        self.person = self.store.save(dict(employee_id='00000001',employee_name='Example',password='synthetic-secret',run_time='07:15',execution_mode='run'))

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
        for change in [{'run_time':'24:00'},{'run_days':[True]},{'employee_id':'changed'},{'run_days':[]},{'execution_mode':'invalid'},{'execution_mode':None}]:
            with self.assertRaises(ValueError):self.store.save(change,self.person['id'])
        self.assertEqual(self.store.credentials(self.person['id'])[2],'synthetic-secret')

    def test_new_accounts_default_to_check_and_reject_answering(self):
        person = self.store.save(dict(employee_id='00000002',employee_name='Viewer',password='synthetic-secret'))
        self.assertEqual(person['execution_mode'],'check')
        with self.assertRaisesRegex(ValueError,'仅查看进度'):
            self.store.enqueue(person['id'],'run')
        self.store.enqueue(person['id'])
        self.assertEqual(self.store.claim()['mode'],'check')

    def test_schedule_uses_each_persons_mode_even_after_viewer_is_complete(self):
        viewer = self.store.save(dict(employee_id='00000002',employee_name='Viewer',password='synthetic-secret'))
        at = datetime(2026,10,15,8,0,tzinfo=TZ)
        with self.store.connect() as db:
            db.execute('UPDATE accounts SET checked_day=?,completion=1 WHERE id=?',(at.date().isoformat(),viewer['id']))
        self.store.schedule(at)
        self.store.schedule(at)
        jobs = {job['account_id']:job['mode'] for job in self.store.history()}
        self.assertEqual(jobs,{self.person['id']:'run',viewer['id']:'check'})
        self.assertEqual(len(self.store.history()),2)

    def test_switching_to_check_downgrades_queue_and_persists(self):
        job = self.store.enqueue(self.person['id'],'run')
        self.store.save({'execution_mode':'check'},self.person['id'])
        reloaded = Store(self.temp.name)
        self.assertEqual(reloaded.accounts()[0]['execution_mode'],'check')
        self.assertEqual(reloaded.history()[0]['mode'],'check')
        self.assertEqual(reloaded.claim()['mode'],'check')
        self.assertEqual(reloaded.history()[0]['id'],job)

    def test_cannot_switch_to_check_while_answering_is_running(self):
        self.store.enqueue(self.person['id'],'run');self.store.claim()
        with self.assertRaisesRegex(ValueError,'正在执行'):
            self.store.save({'execution_mode':'check','team':'Should roll back'},self.person['id'])
        person = self.store.accounts()[0]
        self.assertEqual(person['execution_mode'],'run')
        self.assertEqual(person['team'],'')

    def test_claim_defends_against_legacy_queued_answering_for_viewer(self):
        self.store.enqueue(self.person['id'],'run')
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET execution_mode='check' WHERE id=?",(self.person['id'],))
        self.assertEqual(self.store.claim()['mode'],'check')
        self.assertEqual(self.store.history()[0]['mode'],'check')

    def test_old_schema_upgrade_preserves_people_credentials_and_history(self):
        self.store.enqueue(self.person['id'],'check')
        with self.store.connect() as db:
            db.execute('ALTER TABLE accounts DROP COLUMN execution_mode')
        migrated = Store(self.temp.name)
        self.assertEqual(migrated.accounts()[0]['execution_mode'],'run')
        self.assertEqual(migrated.credentials(self.person['id'])[2],'synthetic-secret')
        self.assertEqual(len(migrated.history()),1)
        self.assertEqual(Store(self.temp.name).accounts()[0]['execution_mode'],'run')


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

    def test_viewer_mode_cannot_be_bypassed_and_default_job_only_checks(self):
        created = self.client.post('/api/daily-practice/accounts',json={
            'employee_id':'00000002','employee_name':'Viewer','password':'synthetic-secret',
        },headers=self.headers)
        self.assertEqual(created.json['data']['execution_mode'],'check')
        path = '/api/daily-practice/accounts/'+str(created.json['data']['id'])
        denied = self.client.post(path+'/run',json={'mode':'run'},headers=self.headers)
        self.assertEqual(denied.status_code,400)
        store = self.app.extensions['daily_store']
        self.assertEqual(store.history(),[])
        self.assertEqual(self.client.post(path+'/run',json={},headers=self.headers).status_code,200)
        job = store.claim();self.assertEqual(job['mode'],'check');store.finish(job['id'],completed=False)
        changed = self.client.put(path,json={'execution_mode':'run'},headers=self.headers)
        self.assertEqual(changed.json['data']['execution_mode'],'run')
        self.assertEqual(self.client.post(path+'/run',json={},headers=self.headers).status_code,200)
        self.assertEqual(store.claim()['mode'],'run')


if __name__=='__main__':unittest.main()
