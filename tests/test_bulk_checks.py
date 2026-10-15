from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
import tempfile
import unittest

from flask import jsonify,request

from management.app import create_app
from management.store import Store,TZ


class BulkCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.at = datetime(2026,10,15,17,0,tzinfo=TZ)

    def tearDown(self):
        self.temp.cleanup()

    def person(self,number,**changes):
        return self.store.save(dict(employee_id=f'{number:08d}',employee_name=f'Example {number}',
                                    password='synthetic-secret',**changes))['id']

    def checked(self,person,day,completed):
        with self.store.connect() as db:
            db.execute('UPDATE accounts SET checked_day=?,completion=? WHERE id=?',(day,completed,person))

    def test_batch_selects_pending_unknown_stale_and_failed_as_checks(self):
        day = self.at.date().isoformat()
        pending = self.person(1,execution_mode='run')
        self.checked(pending,day,False)
        unknown = self.person(2)
        stale = self.person(3)
        self.checked(stale,(self.at-timedelta(days=1)).date().isoformat(),True)
        failed = self.person(4)
        self.store.enqueue(failed,'check',at=self.at)
        self.store.claim();self.store.finish(self.store.history()[0]['id'],error='Synthetic failure')
        completed = self.person(5);self.checked(completed,day,True)
        self.person(6,enabled=False)
        queued = self.person(7);existing = self.store.enqueue(queued,'check',at=self.at)
        deleted = self.person(8);self.store.delete(deleted)
        result = self.store.enqueue_uncompleted_checks(self.at)
        self.assertEqual(result,dict(queued=4,completed=1,paused=1,active=1))
        jobs = [j for j in self.store.history() if j['status']=='queued' and j['id']!=existing]
        self.assertEqual({j['account_id'] for j in jobs},{pending,unknown,stale,failed})
        self.assertTrue(all(j['mode']=='check' and j['trigger']=='manual' and j['day']==day for j in jobs))
        self.assertEqual(self.store.enqueue_uncompleted_checks(self.at),dict(queued=0,completed=1,paused=1,active=5))

    def test_concurrent_clicks_do_not_duplicate_or_change_active_answer_jobs(self):
        running = self.person(1,execution_mode='run')
        self.store.enqueue(running,'run',at=self.at);self.store.claim()
        self.person(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.store.enqueue_uncompleted_checks(self.at),range(2)))
        self.assertEqual(sum(r['queued'] for r in results),1)
        jobs = self.store.history()
        self.assertEqual(len(jobs),2)
        original = next(j for j in jobs if j['account_id']==running)
        self.assertEqual((original['mode'],original['status']),('run','running'))

    def test_admin_endpoint_rejects_anonymous_cross_site_and_mode_override(self):
        def authenticate(function):
            def protected():
                if request.headers.get('X-Test-Role')!='admin':
                    return jsonify(code=403),403
                return function()
            protected.__name__ = function.__name__
            return protected
        app = create_app(self.temp.name,authenticate)
        client = app.test_client();path = '/api/daily-practice/check-uncompleted'
        headers = {'X-Test-Role':'admin'}
        self.person(1,execution_mode='run')
        self.assertEqual(client.post(path,json={}).status_code,403)
        self.assertEqual(client.post(path,json={},headers={**headers,'Origin':'https://external.example'}).status_code,403)
        for body in ({'mode':'run'},[],None):
            self.assertEqual(client.post(path,json=body,headers=headers).status_code,400)
        self.assertEqual(self.store.history(),[])
        response = client.post(path,json={},headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['data']['queued'],1)
        self.assertEqual(self.store.history()[0]['mode'],'check')


if __name__=='__main__':unittest.main()
