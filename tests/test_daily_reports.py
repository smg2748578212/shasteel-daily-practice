from datetime import datetime, timedelta
import os
import tempfile
import unittest
from unittest.mock import patch

from management.store import Store, TZ
from management.worker import Worker
from management.reports import format_daily_report


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.at=datetime(2026,10,15,15,0,tzinfo=TZ)
        self.clock=patch('management.store.now',return_value=self.at);self.clock.start();self.addCleanup(self.clock.stop)
        self.store=Store(self.temp.name)
        for number,name in [(1,'已完成甲'),(2,'未完成乙'),(3,'查询失败丙')]:
            self.store.save({'employee_id':f'{number:08}','employee_name':name,'password':'synthetic-secret','run_time':'16:00'})

    def finish_batch(self):
        self.store.schedule(self.at.replace(hour=16))
        while True:
            job=self.store.claim()
            if not job:break
            if job['account_id']==3:self.store.finish(job['id'],error='Synthetic failure')
            else:self.store.finish(job['id'],completed=job['account_id']==1)

    def test_summary_waits_for_entire_batch_and_lists_only_unfinished_or_unknown_names(self):
        self.assertIsNone(self.store.claim_daily_report(self.at))
        self.assertIsNone(self.store.claim_daily_report(self.at.replace(hour=16)))
        self.store.schedule(self.at.replace(hour=16))
        job=self.store.claim();self.store.finish(job['id'],completed=True)
        self.assertIsNone(self.store.claim_daily_report(self.at.replace(hour=16)))
        while True:
            job=self.store.claim()
            if not job:break
            self.store.finish(job['id'],completed=False if job['account_id']==2 else None,error='Synthetic failure' if job['account_id']==3 else None)
        report=self.store.claim_daily_report(self.at.replace(hour=16))
        self.assertIn('已完成：1 人',report['content']);self.assertNotIn('已完成甲',report['content'])
        self.assertIn('未完成：1 人（未完成乙）',report['content'])
        self.assertIn('查询失败：1 人（查询失败丙）',report['content'])
        self.assertIsNone(self.store.claim_daily_report(self.at.replace(hour=17)))

    def test_report_does_not_repeat_after_restart_or_uncertain_delivery(self):
        self.finish_batch();report=self.store.claim_daily_report(self.at.replace(hour=16))
        reloaded=Store(self.temp.name)
        self.assertIsNone(reloaded.claim_daily_report(self.at.replace(hour=17)))
        self.store.finish_daily_report(report['day'],False)
        self.assertIsNone(reloaded.claim_daily_report(self.at.replace(hour=18)))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT status FROM daily_reports').fetchone()[0],'failed')

    def test_worker_sends_one_qq_summary_for_multiple_people(self):
        self.finish_batch();worker=Worker(self.store)
        env={key:'synthetic-notification-setting' for key in ['ASTROBOT_API_URL','ASTROBOT_API_KEY','ASTROBOT_TARGETS']}
        with patch.dict(os.environ,env),patch('management.store.now',return_value=self.at.replace(hour=16)),patch('management.worker.send',return_value=True) as send:
            worker.notify_daily_summary();worker.notify_daily_summary()
            Worker(Store(self.temp.name)).notify_daily_summary()
        self.assertEqual(send.call_count,1)
        self.assertIn('已完成：1 人',send.call_args.args[1])
        self.assertNotIn('已完成甲',send.call_args.args[1])

    def test_first_install_after_16_does_not_send_backlog(self):
        with tempfile.TemporaryDirectory() as folder,patch('management.store.now',return_value=self.at.replace(hour=17)):
            store=Store(folder)
            store.save({'employee_id':'00000009','employee_name':'Example','password':'synthetic-secret','enabled':True,'run_time':'18:00'})
            self.assertIsNone(store.claim_daily_report(self.at.replace(hour=17)))
            tomorrow=self.at.replace(hour=16)+timedelta(days=1)
            self.assertIsNotNone(store.claim_daily_report(tomorrow))

    def test_unknown_or_failed_people_are_never_counted_as_unfinished(self):
        people=[dict(employee_name='未查询丁',employee_id='00000001',checked_day=None,completion=None,checked_at=None),
                dict(employee_name='旧结果戊',employee_id='00000002',checked_day='2026-10-15',completion=1,checked_at='2026-10-15T07:00:00+08:00',last_status='failed',last_finished_at='2026-10-15T16:00:00+08:00')]
        report=format_daily_report(people,'2026-10-15')
        self.assertIn('已完成：0 人',report['content']);self.assertIn('未完成：0 人',report['content'])
        self.assertIn('查询失败：1 人（旧结果戊）',report['content']);self.assertIn('尚未查询：1 人（未查询丁）',report['content'])

    def test_next_day_can_send_new_summary_and_disabled_people_are_excluded(self):
        self.store.save({'enabled':False},3)
        self.finish_batch()
        first=self.store.claim_daily_report(self.at.replace(hour=16));self.assertIn('统计人数：2 人',first['content'])
        self.assertNotIn('查询失败丙',first['content'])
        next_day=self.at.replace(hour=16)+timedelta(days=1)
        self.assertIsNone(self.store.claim_daily_report(next_day))
        self.store.schedule(next_day)
        while True:
            job=self.store.claim()
            if not job:break
            self.store.finish(job['id'],completed=True)
        self.assertIsNotNone(self.store.claim_daily_report(next_day))


if __name__=='__main__':unittest.main()
