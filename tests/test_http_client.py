import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock,patch

import config
from auth import encode_password
from exceptions import LoginError,NetworkError
from management.execute import main
from management.http_client import PortalClient


def response(value,status=200):
    result=Mock(status_code=status);result.json.return_value=value;return result


class HTTPClientTests(unittest.TestCase):
    def setUp(self):
        self.settings=patch.multiple(config,LOGIN_URL='https://portal.example/login',SESSION_API_URL='https://portal.example/session',
                                    COMPLETION_API_URL='https://practice.example/complete',APP_ID_STR='example-app')
        self.settings.start();self.addCleanup(self.settings.stop)
        self.session=Mock()
        self.identity=dict(status=1,data=dict(userName='00000001',userUniqueToken='synthetic-identity-token'))
        self.token=dict(status=1,data='synthetic-query-token')

    def client(self):return PortalClient('00000001','synthetic-password',session=self.session)

    def test_three_http_calls_verify_identity_before_reading_completion(self):
        self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=200,data=True))]
        with self.client() as client:self.assertIs(client.completed(),True)
        calls=self.session.post.call_args_list
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[0].args[0],config.LOGIN_URL)
        self.assertEqual(calls[0].kwargs['json'],dict(userName='00000001',password=encode_password('synthetic-password')))
        self.assertEqual(calls[1].args[0],config.SESSION_API_URL)
        self.assertEqual(calls[1].kwargs['params'],dict(userName='00000001',appId='example-app'))
        self.assertEqual(calls[2].args[0],config.COMPLETION_API_URL)
        self.assertEqual(calls[2].kwargs['headers'],{'Authorization':'synthetic-query-token'})
        self.assertFalse(calls[2].kwargs['allow_redirects']);self.session.close.assert_called_once()

    def test_failed_or_mismatched_login_never_requests_a_query_token(self):
        for identity in [dict(status=0,msg='synthetic-private-detail'),dict(status=1,data=dict(userName='00000002',userUniqueToken='synthetic-token'))]:
            with self.subTest(identity=identity):
                self.session.reset_mock();self.session.post.side_effect=None;self.session.post.return_value=response(identity)
                with self.assertRaises(LoginError):self.client().completed()
                self.assertEqual(self.session.post.call_count,1)

    def test_only_boolean_completion_can_be_reported(self):
        for value in [True,False,'true',1,None,{},[]]:
            self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=200,data=value))]
            if type(value) is bool:self.assertIs(self.client().completed(),value)
            else:
                with self.assertRaises(ValueError):self.client().completed()

    def test_invalid_token_response_does_not_query_or_submit_answers(self):
        for token in ['',None,{},'bad\r\ntoken']:
            self.session.reset_mock();self.session.post.side_effect=[response(self.identity),response(dict(status=1,data=token))]
            with self.assertRaises(LoginError):self.client().completed()
            self.assertEqual(self.session.post.call_count,2)

    def test_http_errors_do_not_become_completed_results(self):
        for code,error in [(401,LoginError),(403,LoginError),(429,NetworkError),(500,NetworkError),(302,ValueError)]:
            self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=200,data=True),status=code)]
            with self.assertRaises(error):self.client().completed()
        self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=401,data=True))]
        with self.assertRaises(LoginError):self.client().completed()

    def test_network_exception_and_invalid_json_do_not_leak_upstream_details(self):
        import requests
        self.session.post.side_effect=[response(self.identity),requests.ConnectionError('synthetic-private-detail')]
        with self.assertRaises(NetworkError) as raised:self.client().completed()
        self.assertNotIn('private-detail',str(raised.exception))
        invalid=response(None);invalid.json.side_effect=ValueError('synthetic-private-detail')
        self.session.post.side_effect=[response(self.identity),invalid]
        with self.assertRaises(ValueError) as raised:self.client().completed()
        self.assertNotIn('private-detail',str(raised.exception))

    def test_query_mode_emits_result_without_importing_browser_or_model_dependencies(self):
        # A subprocess starts with an empty module cache, so imports cannot pass accidentally.
        code=r'''
import builtins,contextlib,io,json,sys
from unittest.mock import patch
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in {'playwright','browser_manager','daily_practice','openai','llm_engine','question_bank'}:
  raise AssertionError('Browser or answer dependency imported during HTTP query')
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from management.execute import main
output=io.StringIO()
with patch('management.execute.PortalClient') as client,contextlib.redirect_stdout(output):
 client.return_value.__enter__.return_value.completed.return_value=False
 main('check')
values=[json.loads(line) for line in output.getvalue().splitlines()]
assert values==[dict(type='result',completed=False)]
print('HTTP_CHECK_HAS_NO_BROWSER_IMPORTS')
'''
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('HTTP_CHECK_HAS_NO_BROWSER_IMPORTS',result.stdout)

    def test_already_completed_auto_run_returns_before_browser_import(self):
        output=io.StringIO()
        with patch('management.execute.PortalClient') as client,contextlib.redirect_stdout(output):
            client.return_value.__enter__.return_value.completed.return_value=True
            main('run')
        self.assertEqual(json.loads(output.getvalue()),dict(type='result',completed=True))

    def test_query_failure_and_unknown_modes_never_fall_back_to_answering(self):
        with patch('management.execute.PortalClient') as client:
            client.return_value.__enter__.return_value.completed.side_effect=NetworkError('Synthetic failure')
            with self.assertRaises(NetworkError):main('check')
        with patch('management.execute.PortalClient') as client:
            with self.assertRaises(ValueError):main('unknown')
            client.assert_not_called()



class TokenCacheTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        HTTPClientTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'http_session.json'

    def client(self,user='00000001',version='synthetic-credential-version'):
        return PortalClient(user,'synthetic-password',session=self.session,cache_path=self.path,credential_version=version)

    def seed(self,token='synthetic-cached-token'):
        self.client().save_token(token)

    def test_valid_per_person_cache_uses_only_one_completion_request(self):
        self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=200,data=True))]
        self.assertTrue(self.client().completed());self.assertTrue(self.path.exists())
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.session.reset_mock();self.session.post.side_effect=[response(dict(code=200,data=False))]
        reused=self.client();self.assertFalse(reused.completed());self.assertTrue(reused.used_cached_token)
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(self.session.post.call_args.args[0],config.COMPLETION_API_URL)
        self.assertEqual(self.session.post.call_args.kwargs['headers'],{'Authorization':'synthetic-query-token'})
        self.assertNotIn('synthetic-password',self.path.read_text())

    def test_nearly_expired_jwt_skips_cache_and_logs_in_again(self):
        import base64,time
        payload=base64.urlsafe_b64encode(json.dumps({'exp':time.time()+30}).encode()).decode().rstrip('=')
        self.seed('header.'+payload+'.signature')
        self.session.post.side_effect=[response(self.identity),response(self.token),response(dict(code=200,data=True))]
        self.assertTrue(self.client().completed());self.assertEqual(self.session.post.call_count,3)
        self.assertEqual(self.session.post.call_args_list[0].args[0],config.LOGIN_URL)

    def test_server_rejected_cache_refreshes_once_and_retries_the_query(self):
        self.seed()
        self.session.post.side_effect=[response(dict(code=401,data=None)),response(self.identity),response(self.token),response(dict(code=200,data=True))]
        client=self.client();self.assertTrue(client.completed());self.assertFalse(client.used_cached_token)
        self.assertEqual(self.session.post.call_count,4)
        self.assertEqual(self.client().load_token(),'synthetic-query-token')

    def test_network_failure_preserves_cache_without_relogging(self):
        import requests
        self.seed();before=self.path.read_bytes()
        self.session.post.side_effect=requests.ReadTimeout('synthetic-private-detail')
        with self.assertRaises(NetworkError):self.client().completed()
        self.assertEqual(self.session.post.call_count,1);self.assertEqual(self.path.read_bytes(),before)
        self.session.reset_mock();self.session.post.side_effect=[response(dict(code=200,data=True))]
        self.assertTrue(self.client().completed());self.assertEqual(self.session.post.call_count,1)

    def test_connect_timeout_retries_once_without_refreshing_token(self):
        import requests
        self.seed()
        self.session.post.side_effect=[requests.ConnectTimeout('synthetic-timeout'),response(dict(code=200,data=True))]
        client=self.client();self.assertTrue(client.completed());self.assertTrue(client.used_cached_token)
        self.assertEqual(self.session.post.call_count,2)
        self.assertTrue(all(call.args[0]==config.COMPLETION_API_URL for call in self.session.post.call_args_list))

    def test_fresh_token_rejection_does_not_loop_or_keep_rejected_tokens(self):
        self.seed()
        self.session.post.side_effect=[response(None,401),response(self.identity),response(self.token),response(None,401)]
        with self.assertRaises(LoginError):self.client().completed()
        self.assertEqual(self.session.post.call_count,4);self.assertFalse(self.path.exists())

    def test_tokens_cannot_cross_users_password_versions_or_endpoint_scopes(self):
        self.seed()
        self.assertIsNone(self.client(user='00000002').load_token())
        self.assertIsNone(self.client(version='different-password-version').load_token())
        with patch.object(config,'COMPLETION_API_URL','https://another.example/complete'):
            self.assertIsNone(self.client().load_token())
        self.assertEqual(self.client().load_token(),'synthetic-cached-token')

    def test_insecure_permissions_symlinks_and_corrupt_cache_are_not_read(self):
        self.seed();self.path.chmod(0o644);self.assertIsNone(self.client().load_token())
        self.path.chmod(0o600)
        link=self.path.with_name('link.json');link.symlink_to(self.path)
        client=PortalClient('00000001','synthetic-password',session=self.session,cache_path=link,credential_version='synthetic-credential-version')
        self.assertIsNone(client.load_token())
        self.path.write_text('invalid-json');self.assertIsNone(self.client().load_token())

    def test_unknown_expiry_uses_a_bounded_cache_lifetime(self):
        with patch('management.http_client.time.time',return_value=1000):self.seed()
        with patch('management.http_client.time.time',return_value=2000):self.assertIsNotNone(self.client().load_token())
        with patch('management.http_client.time.time',return_value=1000+86400):self.assertIsNone(self.client().load_token())

    def test_password_change_invalidates_version_but_name_change_does_not(self):
        from management.store import Store
        store=Store(self.temp.name)
        person=store.save(dict(employee_id='00000001',employee_name='Example',password='synthetic-password'))
        original=store.execution_credentials(person['id'])
        self.assertEqual(store.credentials(person['id']),original[:3])
        store.save({'employee_name':'Renamed'},person['id'])
        self.assertEqual(store.execution_credentials(person['id'])[3],original[3])
        store.save({'password':'synthetic-new-password'},person['id'])
        self.assertNotEqual(store.execution_credentials(person['id'])[3],original[3])


if __name__=='__main__':unittest.main()
