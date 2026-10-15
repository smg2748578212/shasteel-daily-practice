"""Run the existing daily-practice account and protected extra accounts serially.

Account passwords are read from a mode-0600 JSON file and passed only through
each child's environment. Runtime output intentionally contains no credentials.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


CHILD_MARKER = "DAILY_PRACTICE_ACCOUNT_RUN"
STATE_ENV = "DAILY_PRACTICE_STATE_DIR"
LOCK_PATH = Path("/tmp/daily_practice_accounts.lock")
MAX_PROFILE_BYTES = 1024 * 1024
_EMPLOYEE_ID = re.compile(r"[0-9]{1,20}\Z")


class ProfileError(ValueError):
    """A deliberately nonsensitive configuration error code."""


@dataclass(frozen=True)
class AccountProfile:
    employee_id: str
    password: str = field(repr=False)
    employee_name: str = ""


def validate_args(argv: Sequence[str]) -> list[str]:
    args = list(argv)
    if args not in ([], ["--dry-run"]):
        raise ProfileError("arguments_rejected")
    return args


def validate_profiles(data: object, legacy_id: str) -> list[AccountProfile]:
    if not isinstance(legacy_id, str) or not _EMPLOYEE_ID.fullmatch(legacy_id):
        raise ProfileError("legacy_account_invalid")
    if not isinstance(data, list) or len(data) > 100:
        raise ProfileError("profiles_invalid")
    profiles = []
    seen = {legacy_id}
    for item in data:
        if not isinstance(item, dict) or set(item) - {
            "employee_id", "password", "employee_name"
        }:
            raise ProfileError("profiles_invalid")
        employee_id = item.get("employee_id")
        password = item.get("password")
        name = item.get("employee_name", "")
        if not isinstance(employee_id, str) or not _EMPLOYEE_ID.fullmatch(employee_id):
            raise ProfileError("profiles_invalid")
        if employee_id in seen:
            raise ProfileError("profiles_duplicate")
        if not isinstance(password, str) or not password or "\0" in password:
            raise ProfileError("profiles_invalid")
        if not isinstance(name, str) or len(name) > 128 or any(
            ord(char) < 32 for char in name
        ):
            raise ProfileError("profiles_invalid")
        seen.add(employee_id)
        profiles.append(AccountProfile(employee_id, password, name))
    return profiles


def _check_profile_stat(file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise ProfileError("profiles_not_regular")
    if os.name == "posix":
        if stat.S_IMODE(file_stat.st_mode) != 0o600:
            raise ProfileError("profiles_permissions")
        if file_stat.st_uid != os.geteuid():
            raise ProfileError("profiles_owner")


def load_profiles(path: Path, legacy_id: str) -> list[AccountProfile]:
    # Never follow a credential-file symlink. fstat also checks the opened file,
    # rather than trusting a separate path check.
    if path.is_symlink():
        raise ProfileError("profiles_symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return validate_profiles([], legacy_id)
    except OSError:
        raise ProfileError("profiles_unreadable") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            _check_profile_stat(os.fstat(stream.fileno()))
            raw = stream.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            raise ProfileError("profiles_too_large")
        data = json.loads(raw.decode("utf-8"))
    except ProfileError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise ProfileError("profiles_invalid") from None
    return validate_profiles(data, legacy_id)


def acquire_lock(path: Path = LOCK_PATH):
    """Hold a nonblocking Unix flock for the entire serial account batch."""
    import fcntl

    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    stream = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        return None
    except Exception:
        stream.close()
        raise
    return stream


def run_accounts(
    args: Sequence[str],
    profiles: Sequence[AccountProfile],
    legacy_id: str,
    *,
    base_dir: Path,
    child_runner: Callable = subprocess.run,
    output: Callable[[str], None] = print,
) -> int:
    args = validate_args(args)
    # Revalidate programmatic callers before spawning any account.
    validate_profiles(
        [{"employee_id": p.employee_id, "password": p.password,
          "employee_name": p.employee_name} for p in profiles], legacy_id
    )
    base_dir = base_dir.resolve()
    command = [sys.executable, str(base_dir / "run.py"), *args]
    base_env = dict(os.environ)
    base_env[CHILD_MARKER] = "1"
    for name in ("SHASTEEL_EMPLOYEE_ID", "SHASTEEL_EMPLOYEE_NAME", STATE_ENV):
        base_env.pop(name, None)
    # Legacy configuration and state files are retained exactly where they were.
    accounts: list[AccountProfile | None] = [None, *profiles]
    failed = False
    for profile in accounts:
        employee_id = profile.employee_id if profile else legacy_id
        env = base_env.copy()
        if profile:
            env["SHASTEEL_EMPLOYEE_ID"] = profile.employee_id
            env["SHASTEEL_EMPLOYEE_NAME"] = profile.employee_name or profile.employee_id
            env["SHASTEEL_PASSWORD"] = profile.password
            env[STATE_ENV] = str(base_dir / "accounts" / profile.employee_id)
        try:
            result = child_runner(
                command, cwd=str(base_dir), env=env, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            returncode = int(result.returncode)
        except Exception:
            # Exception messages can contain connection URLs or other secrets.
            returncode = 125
        failed = failed or returncode != 0
        output(json.dumps({"employee_id": employee_id, "returncode": returncode}))
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = validate_args(sys.argv[1:] if argv is None else argv)
        base_dir = Path(__file__).resolve().parent
        config = importlib.import_module("config")
        legacy_id = getattr(config, "DEFAULT_EMPLOYEE_ID", config.EMPLOYEE_ID)
        profiles = load_profiles(base_dir / "extra_accounts.json", legacy_id)
    except ProfileError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"error": "configuration_unavailable"}))
        return 2
    try:
        lock = acquire_lock()
    except Exception:
        print(json.dumps({"error": "batch_lock_unavailable"}))
        return 2
    if lock is None:
        print(json.dumps({"error": "batch_already_running"}))
        return 75
    try:
        return run_accounts(args, profiles, legacy_id, base_dir=base_dir)
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
