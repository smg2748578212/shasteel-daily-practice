import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import config
from exceptions import NetworkError,NoQuestionsTodayError,SubmissionError
from management.answer_api import answer_records,complete_practice
from management.http_client import PortalClient
from management.execute import main


def paper():
    return dict(code=200,data=[
        dict(id='synthetic-q1',question='Synthetic first question',answer='B',options=[dict(option='A',content='First'),dict(option='B',content='Second')]),
        dict(id='synthetic-q2',question='Synthetic second question',answer='A',options=[dict(option='A',content='True'),dict(option='B',content='False')]),
    ])


class AnswerAPITests(unittest.TestCase):
    def setUp(self):
        self.settings=patch.multiple(config,SESSION_API_URL='https://portal.example/session',GENERATE_API_URL='https://practice.example/generate',SUBMIT_API_URL='https://practice.example/submit')
        self.settings.start();self.addCleanup(self.settings.stop)
        self.client=Mock()
        self.client.authorized_post.side_effect=[paper(),dict(code=200,data=True)]
        self.client.completed.return_value=True
        self.progress=Mock()

    def test_answers_follow_question_ids_and_original_order(self):
        self.assertTrue(complete_practice(self.client,self.progress))
        calls=self.client.authorized_post.call_args_list
        self.assertEqual(len(calls),2);self.assertEqual(calls[0].args,(config.GENERATE_API_URL,))
        self.assertEqual(calls[1].args[0],config.SUBMIT_API_URL)
        payload=calls[1].args[1]
        self.assertEqual(payload['answerRecords'],[dict(questionId='synthetic-q1',answer='B'),dict(questionId='synthetic-q2',answer='A')])
        self.assertRegex(payload['startTime'],r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')
        self.client.completed.assert_called_once()

    def test_empty_missing_invalid_or_duplicate_answers_are_never_submitted(self):
        cases=[[],[dict(id='synthetic-id',answer='A',options=[])],paper()['data']+[paper()['data'][0]],
               [dict(id='synthetic-id',options=[dict(option='A',content='First')])]]
        for questions in cases:
            with self.subTest(questions=questions):
                client=Mock();client.authorized_post.return_value=dict(code=200,data=questions)
                with self.assertRaises((ValueError,NoQuestionsTodayError)):complete_practice(client,self.progress)
                self.assertEqual(client.authorized_post.call_count,1)
                client.completed.assert_not_called()

    def test_submission_success_alone_does_not_prove_completion(self):
        self.client.completed.return_value=False
        with patch('management.answer_api.time.sleep'):
            self.assertFalse(complete_practice(self.client,self.progress))
        self.assertEqual(self.client.completed.call_count,3)
        self.assertEqual(self.client.authorized_post.call_count,2)

    def test_delayed_completion_is_rechecked_without_resubmitting(self):
        self.client.completed.side_effect=[False,True]
        with patch('management.answer_api.time.sleep'):
            self.assertTrue(complete_practice(self.client,self.progress))
        self.assertEqual(self.client.authorized_post.call_count,2)

    def test_lost_submission_response_checks_completion_without_repeating_write(self):
        self.client.authorized_post.side_effect=[paper(),NetworkError('Synthetic timeout')]
        self.assertTrue(complete_practice(self.client,self.progress))
        self.assertEqual(self.client.authorized_post.call_count,2)

    def test_unconfirmed_submission_stops_without_another_submit(self):
        self.client.authorized_post.side_effect=[paper(),NetworkError('Synthetic timeout')]
        self.client.completed.return_value=False
        with self.assertRaises(SubmissionError):complete_practice(self.client,self.progress)
        self.assertEqual(self.client.authorized_post.call_count,2)

    def test_rejected_submission_is_not_repeated(self):
        self.client.authorized_post.side_effect=[paper(),dict(code=500,data=None)]
        self.client.completed.return_value=False
        with self.assertRaises(SubmissionError):complete_practice(self.client,self.progress)
        self.assertEqual(self.client.authorized_post.call_count,2)

    def test_mutating_http_requests_are_not_retried_after_connect_timeout(self):
        import requests
        session=Mock();session.post.side_effect=requests.ConnectTimeout('Synthetic timeout')
        client=PortalClient('00000001','synthetic-password',session=session)
        client.token='synthetic-query-token'
        with self.assertRaises(NetworkError):client.authorized_post(config.SUBMIT_API_URL,dict(answerRecords=[]))
        self.assertEqual(session.post.call_count,1)

    def test_check_mode_never_generates_or_submits_questions(self):
        with patch('management.execute.PortalClient') as client,patch('management.answer_api.complete_practice') as answer,contextlib.redirect_stdout(io.StringIO()):
            client.return_value.__enter__.return_value.completed.return_value=False
            main('check')
        answer.assert_not_called()

    def test_already_completed_run_skips_all_answer_endpoints(self):
        with patch('management.execute.PortalClient') as client,patch('management.answer_api.complete_practice') as answer,contextlib.redirect_stdout(io.StringIO()):
            client.return_value.__enter__.return_value.completed.return_value=True
            main('run')
        answer.assert_not_called()

    def test_complete_http_answer_flow_does_not_import_browser_model_or_question_bank(self):
        code=r'''
import builtins,contextlib,io,json,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
original=builtins.__import__
def guard(name,*args,**kwargs):
 if name.split('.')[0] in {'playwright','browser_manager','daily_practice','openai','llm_engine','question_bank','ocr_engine'}:
  raise AssertionError('Heavy answer dependency imported')
 return original(name,*args,**kwargs)
builtins.__import__=guard
import config
from management.http_client import PortalClient
from management.execute import main
def response(value):
 result=Mock(status_code=200);result.json.return_value=value;return result
session=Mock()
session.post.side_effect=[
 response(dict(status=1,data=dict(userName='00000001',userUniqueToken='synthetic-token'))),
 response(dict(status=1,data='synthetic-query-token')),
 response(dict(code=200,data=False)),
 response(dict(code=200,data=[dict(id='synthetic-q',answer='B',options=[dict(option='A'),dict(option='B')])])),
 response(dict(code=200,data=True)),response(dict(code=200,data=True)),
]
with tempfile.TemporaryDirectory() as folder,patch.multiple(config,
 LOGIN_URL='https://portal.example/login',SESSION_API_URL='https://portal.example/session',APP_ID_STR='synthetic-app',
 COMPLETION_API_URL='https://practice.example/complete',GENERATE_API_URL='https://practice.example/generate',SUBMIT_API_URL='https://practice.example/submit'):
 client=PortalClient('00000001','synthetic-password',session=session,cache_path=Path(folder)/'token.json',credential_version='synthetic-version')
 output=io.StringIO()
 with patch('management.execute.PortalClient',return_value=client),contextlib.redirect_stdout(output):main('run')
 events=[json.loads(line) for line in output.getvalue().splitlines()]
 assert events[-1]==dict(type='result',completed=True)
 assert session.post.call_count==6
 assert session.post.call_args_list[4].kwargs['json']['answerRecords']==[dict(questionId='synthetic-q',answer='B')]
print('HTTP_ANSWER_WITHOUT_BROWSER_OR_MODEL')
'''
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('HTTP_ANSWER_WITHOUT_BROWSER_OR_MODEL',result.stdout)


if __name__=='__main__':unittest.main()
