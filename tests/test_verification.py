import unittest
from unittest.mock import Mock,patch

import config
from management.execute import verify_completion


class VerificationTests(unittest.TestCase):
    def test_only_an_explicit_portal_boolean_is_accepted(self):
        page=Mock()
        with patch.object(config,'COMPLETION_API_URL','https://portal.example/completion'):
            for completed in [True,False]:
                page.evaluate.return_value={'code':200,'data':completed}
                self.assertIs(verify_completion(page),completed)
            for data in ['true',1,None,{}]:
                page.evaluate.return_value={'code':200,'data':data}
                with self.assertRaises(ValueError):verify_completion(page)

    def test_success_shaped_error_does_not_mark_complete(self):
        page=Mock();page.evaluate.return_value={'code':401,'data':True}
        with patch.object(config,'COMPLETION_API_URL','https://portal.example/completion'):
            with self.assertRaises(ValueError):verify_completion(page)


if __name__=='__main__':unittest.main()
