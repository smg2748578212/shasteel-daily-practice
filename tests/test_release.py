"""Offline checks; no portal login, model calls, or notifications are sent."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import account_runner
import notify
import question_bank
from scripts.create_bank_template import create_template


class ReleaseTests(unittest.TestCase):
    def test_unconfigured_notifications_do_not_contact_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("urllib.request.urlopen") as send:
            self.assertFalse(notify.send("offline check"))
            send.assert_not_called()

    def test_account_batch_isolates_credentials_and_state(self):
        captured = []
        profile = account_runner.AccountProfile("00000002", "synthetic-test-secret", "Test")

        def child(command, **kwargs):
            captured.append(kwargs["env"])
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(os.environ, {"SHASTEEL_PASSWORD": "synthetic-primary-secret"}, clear=True):
            result = account_runner.run_accounts(
                ["--dry-run"], [profile], "00000001", base_dir=Path.cwd(),
                child_runner=child, output=lambda message: None,
            )
        self.assertEqual(result, 0)
        self.assertEqual(captured[0]["SHASTEEL_PASSWORD"], "synthetic-primary-secret")
        self.assertEqual(captured[1]["SHASTEEL_PASSWORD"], "synthetic-test-secret")
        self.assertTrue(captured[1]["DAILY_PRACTICE_STATE_DIR"].endswith("accounts/00000002"))
        self.assertNotIn("DAILY_PRACTICE_STATE_DIR", captured[0])

    def test_profile_permissions_reject_unprotected_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "extra_accounts.json"
            path.write_text(json.dumps([{
                "employee_id": "00000002", "password": "synthetic-test-secret",
            }]))
            path.chmod(0o644)
            with self.assertRaisesRegex(account_runner.ProfileError, "profiles_permissions"):
                account_runner.load_profiles(path, "00000001")

    def test_template_round_trip_and_answer_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bank.xlsx"
            create_template(path)
            with self.assertRaises(FileExistsError):
                create_template(path)
            bank = question_bank.QuestionBank(path)
            self.assertEqual(bank.single_choice_count, 0)
            with patch.object(question_bank, "_bank", bank):
                question_bank.append_question("一加一等于几？", ["A. 2", "B. 3"], "A")
                question_bank.append_question("一加一等于几？", ["A. 2", "B. 3"], "A")
                question_bank.append_question("水是液体。", ["A. 对", "B. 错"], "对", "判断题")
            reread = question_bank.QuestionBank(path)
            self.assertEqual(reread.single_choice_count, 1)
            self.assertEqual(reread.true_false_count, 1)
            self.assertEqual(reread.find_answer("一加一等于几？")["answer"], "A")
            self.assertEqual(reread.find_answer("水是液体。")["answer"], "A")

    def test_importing_config_has_no_identity_or_directory_side_effect(self):
        code = (
            "import config,json; "
            "print(json.dumps([config.EMPLOYEE_ID,config.EMPLOYEE_NAME,config.PASSWORD]))"
        )
        with tempfile.TemporaryDirectory() as folder:
            env = {key: value for key, value in os.environ.items() if not key.startswith("SHASTEEL_")}
            env["DAILY_PRACTICE_STATE_DIR"] = str(Path(folder) / "not-created")
            result = subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout), ["", "", ""])
            self.assertFalse(Path(env["DAILY_PRACTICE_STATE_DIR"]).exists())

    def test_first_run_creates_isolated_account_lock_directory(self):
        code = "import run; assert run.acquire_lock(); run.release_lock()"
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "new-account"
            env = dict(os.environ, DAILY_PRACTICE_STATE_DIR=str(state))
            subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True, text=True)
            self.assertTrue((state / "run.lock").is_file())


if __name__ == "__main__":
    unittest.main()
