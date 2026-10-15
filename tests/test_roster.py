from datetime import datetime,timedelta
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from flask import jsonify

from management.app import create_app
from management.store import Store,TZ


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.at=datetime(2026,10,15,15,0,tzinfo=TZ)
        self.clock=patch('management.store.now',return_value=self.at);self.clock.start();self.addCleanup(self.clock.stop)
        def authenticate(function):
            def deny(*args,**kwargs):return jsonify(code=403),403
            deny.__name__=function.__name__;return deny
        self.app=create_app(self.temp.name,authenticate);self.client=self.app.test_client();self.store=self.app.extensions['daily_store']
        for number,name in [(1,'完成甲'),(2,'待完成乙'),(3,'失败丙'),(4,'暂停丁')]:
            self.store.save(dict(employee_id=f'{number:08}',employee_name=name,team='示例班组',password='synthetic-private-secret',run_time='16:00',enabled=number!=4))

    def batch(self):
        at=self.at.replace(hour=16)
        with patch('management.store.now',return_value=at):
            self.store.schedule(at)
            while True:
                job=self.store.claim()
                if not job:break
                self.store.finish(job['id'],completed=job['account_id']==1 if job['account_id']!=3 else None,
                                  error='private-internal-detail' if job['account_id']==3 else None)

    def test_anonymous_full_roster_includes_paused_people_without_account_secrets(self):
        response=self.client.get('/api/daily-practice/public/roster')
        self.assertEqual(response.status_code,200)
        data=response.json['data'];self.assertEqual(data['counts']['total'],4)
        self.assertEqual(data['counts']['paused'],1);self.assertEqual(data['counts']['unknown'],3)
        self.assertEqual({p['name'] for p in data['people']},{'完成甲','待完成乙','失败丙','暂停丁'})
        for person in data['people']:self.assertEqual(set(person),{'name','team','status','checked_at'})
        raw=response.get_data(as_text=True)
        for secret in ['synthetic-private-secret','00000001','employee_id','secret','password','latest_job','private-internal-detail']:
            self.assertNotIn(secret,raw)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['X-Robots-Tag'],'noindex, nofollow')
        self.assertEqual(self.client.get('/api/daily-practice/overview').status_code,403)
        self.assertEqual(self.client.get('/api/daily-practice/history').status_code,403)
        self.assertEqual(self.client.post('/api/daily-practice/public/roster',json={}).status_code,405)

    def test_live_roster_uses_verified_results_and_distinguishes_failures(self):
        self.batch();data=self.client.get('/api/daily-practice/public/roster').json['data']
        self.assertEqual(data['counts'],dict(total=4,completed=1,pending=1,failed=1,paused=1,unknown=0,checking=0))
        self.assertEqual({p['name']:p['status'] for p in data['people']},dict(完成甲='completed',待完成乙='pending',失败丙='failed',暂停丁='paused'))
        self.assertNotIn('private-internal-detail',json.dumps(data))

    def test_qq_link_and_saved_snapshot_include_every_name_and_remain_unchanged(self):
        self.batch()
        with patch.dict(os.environ,{'DAILY_MANAGER_PUBLIC_URL':'https://example.org'}):
            report=self.store.claim_daily_report(self.at.replace(hour=16))
        self.assertIn('查看全部名单：https://example.org/daily-practice-roster.html?date=2026-10-15',report['content'])
        self.assertIn('已完成：1 人',report['content']);self.assertNotIn('完成甲',report['content'])
        original=self.client.get('/api/daily-practice/public/roster?date=2026-10-15').json['data']
        self.assertEqual(original['source'],'snapshot');self.assertEqual(len(original['people']),4)
        self.assertIn('完成甲',{p['name'] for p in original['people']})
        self.store.save({'employee_name':'改名后的人员'},1);self.store.delete(2)
        self.store.save(dict(employee_id='00000005',employee_name='后来加入',password='synthetic-secret'))
        self.assertEqual(self.client.get('/api/daily-practice/public/roster?date=2026-10-15').json['data'],original)
        live=self.client.get('/api/daily-practice/public/roster').json['data']
        self.assertEqual(live['source'],'live');self.assertIn('后来加入',{p['name'] for p in live['people']})
        with patch('management.store.now',return_value=self.at+timedelta(days=1)):
            self.assertEqual(Store(self.temp.name).roster('2026-10-15'),original)

    def test_date_validation_and_unavailable_history_never_invent_results(self):
        path='/api/daily-practice/public/roster'
        self.assertEqual(self.client.get(path+'?date=2026-10-15').json['data']['source'],'live')
        self.assertEqual(self.client.get(path+'?date=2026-10-14').status_code,404)
        for index,query in enumerate(['date=2026-10-16','date=2026-02-30','date=2026-1-1','date=','date=abc','account_id=1','date=2026-10-15&date=2026-10-15']):
            self.assertEqual(self.client.get(path+'?'+query,environ_overrides={'REMOTE_ADDR':f'192.0.2.{index+1}'}).status_code,400,query)
        self.assertEqual(self.store.history(),[])

    def test_previous_days_completion_is_unknown_and_queued_checks_are_distinct(self):
        with self.store.connect() as db:
            db.execute("UPDATE accounts SET checked_day='2026-10-14',completion=1,checked_at='2026-10-14T16:00:00+08:00' WHERE id=1")
        data=self.store.roster();person=next(p for p in data['people'] if p['name']=='完成甲')
        self.assertEqual(person['status'],'unknown');self.assertIsNone(person['checked_at'])
        self.store.enqueue(1,'check');data=self.store.roster()
        self.assertEqual(data['counts']['checking'],1)
        self.assertEqual(next(p for p in data['people'] if p['name']=='完成甲')['status'],'checking')

    def test_roster_reads_never_enqueue_portal_work_or_send_qq(self):
        with patch('management.worker.send') as send:
            for _ in range(3):self.assertEqual(self.client.get('/api/daily-practice/public/roster').status_code,200)
        self.assertEqual(self.store.history(),[]);send.assert_not_called()

    def test_old_report_schema_upgrade_preserves_delivery_state_and_does_not_forge_snapshot(self):
        with self.store.connect() as db:
            db.execute("INSERT INTO daily_reports(day,status,title,content,created_at) VALUES('2026-10-14','sent','Old report','Old content','2026-10-14T16:00:00+08:00')")
            db.execute('ALTER TABLE daily_reports DROP COLUMN roster_json')
        migrated=Store(self.temp.name)
        with migrated.connect() as db:
            row=db.execute("SELECT * FROM daily_reports WHERE day='2026-10-14'").fetchone()
            self.assertEqual((row['status'],row['content'],row['roster_json']),('sent','Old content',None))
        with self.assertRaises(LookupError):migrated.roster('2026-10-14')


if __name__=='__main__':unittest.main()
