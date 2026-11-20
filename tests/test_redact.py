"""Redaction (docs/15 §3.1, docs/13 S1 + L3). No network, no real `.env`."""

from __future__ import annotations

from pathlib import Path

import pytest

from boomerang.config import get_settings
from boomerang.redact import find_secrets, redact, redact_with_map, secret_values

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"                          # AWS's documented example key
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"   # 40 chars, AWS's documented example


@pytest.fixture
def env_file(isolated_settings: Path, monkeypatch: pytest.MonkeyPatch):
    """Write a `.env` (and optional siblings) in a private folder and point the settings at it."""
    folder = isolated_settings / "envdir"
    folder.mkdir()

    def write(text: str, name: str = ".env") -> Path:
        path = folder / name
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("BOOMERANG_DOTENV", str(folder / ".env"))
        get_settings.cache_clear()
        secret_values.cache_clear()
        return path

    yield write
    secret_values.cache_clear()


def _secrets_env(text: str, run_id: str = "r1") -> Path:
    path = get_settings().runs_dir / run_id / "leaseweb" / "secrets.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    secret_values.cache_clear()
    return path


# ---------- each pattern ----------

def test_aws_access_key():
    assert redact(f"key = {AWS_KEY} done") == "key = <REDACTED:aws_access_key> done"
    assert redact("ASIA" + "Q" * 16) == "<REDACTED:aws_access_key>"


@pytest.mark.parametrize("text, expected", [
    (f"AWS_SECRET_ACCESS_KEY={AWS_SECRET}", "AWS_SECRET_ACCESS_KEY=<REDACTED:aws_secret>"),
    (f'aws_secret_access_key="{AWS_SECRET}"', 'aws_secret_access_key="<REDACTED:aws_secret>"'),
    (f'{{"aws_secret_access_key": "{AWS_SECRET}"}}', '{"aws_secret_access_key": "<REDACTED:aws_secret>"}'),
])
def test_aws_secret_after_its_key_any_case(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("DB_PASSWORD=hunter2hunter2", "DB_PASSWORD=<REDACTED:DB_PASSWORD>"),
    ("DB_PASSWORD=12ab34cd", "DB_PASSWORD=<REDACTED:DB_PASSWORD>"),
    ("export API_TOKEN='abcd1234'", "export API_TOKEN='<REDACTED:API_TOKEN>'"),
    ('S3_SECRET_ACCESS_KEY="k3y-v4lue" # comment', 'S3_SECRET_ACCESS_KEY="<REDACTED:S3_SECRET_ACCESS_KEY>" # comment'),
    ("      - POSTGRES_PASSWORD=s3cr3tpass", "      - POSTGRES_PASSWORD=<REDACTED:POSTGRES_PASSWORD>"),
    ("A=1\r\nDB_PASSWORD=abcdabcd\r\nB=2", "A=1\r\nDB_PASSWORD=<REDACTED:DB_PASSWORD>\r\nB=2"),
])
def test_env_style_lines(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text, expected", [
    ('connect(user=u, password="hunter22")', 'connect(user=u, password="<REDACTED:password>")'),
    ("client(api_key='sk-abc123')", "client(api_key='<REDACTED:api_key>')"),
    ('{"db_password": "pa55word!"}', '{"db_password": "<REDACTED:db_password>"}'),
    ("auth_token: 'tok-9876'", "auth_token: '<REDACTED:auth_token>'"),
])
def test_quoted_secret_literals(text, expected):
    assert redact(text) == expected


def test_url_credentials():
    assert (redact("DATABASE_URL=postgresql://shoplite:pw123@db:5432/shoplite")
            == "DATABASE_URL=postgresql://<REDACTED:userinfo>@db:5432/shoplite")
    assert redact("amqp://guest:guest@rabbitmq:5672/") == "amqp://<REDACTED:userinfo>@rabbitmq:5672/"


@pytest.mark.parametrize("text", [
    # code that only looks secret (docs/15 §3.1): never redacted
    "def get_token(self, scope):",
    "conn = connect(user=user, password=password)",
    "    password = password",
    "secret: bytes",
    "token_url = build()",
    "API_TOKEN = get_token()",
    "API_KEY = os.environ['API_KEY']",
    "SIZE_WARN_TOKENS = 6000",
    "MAX_TOKENS=4096\nTOKEN_TTL_S = 0.5",
    "headers = {'token': token}",
    # not credentials
    "http://127.0.0.1:8080/orders",
    "postgresql://app:${DB_PASSWORD}@db:5432/app",
    "PASSWORD_MIN_LENGTH=",
    "TABLE_NAME=orders\nAWS_REGION=us-east-1\nQUEUE=order_events",
])
def test_code_and_names_are_not_redacted(text):
    assert redact(text) == text
    assert find_secrets(text) == []


# ---------- known values (L3) ----------

def test_known_values_from_env_only_secret_keys_and_8_chars(env_file):
    env_file("ORDERS_TABLE=orders\nAWS_REGION=us-east-1\nAWS_SECRET_ACCESS_KEY=test\n"
             "ANTHROPIC_API_KEY=sk-ant-test-1234567890\nSHORT_TOKEN=abc12\nBOOMERANG_MODEL=qwen2.5-coder:7b\n")
    assert secret_values() == {"ANTHROPIC_API_KEY": "sk-ant-test-1234567890"}
    plain = "table orders in us-east-1, creds test/test, model qwen2.5-coder:7b, abc12"
    assert redact(plain) == plain                                        # L3: names are not secrets
    assert redact("use sk-ant-test-1234567890 here") == "use <REDACTED:ANTHROPIC_API_KEY> here"


def test_env_siblings_are_read(env_file):
    env_file("X=1\n")
    env_file("DEPLOY_PASSWORD=from-the-local-file\n", name=".env.local")
    assert redact("x from-the-local-file y") == "x <REDACTED:DEPLOY_PASSWORD> y"


def test_every_value_of_a_secrets_env_is_redacted():
    _secrets_env("POSTGRES_PASSWORD=Zk3pQ9vX2mLw\nRABBITMQ_DEFAULT_PASS=Rb7xYq2Lk9Mn\nPG_SHORT=abc\n")
    text = "pw Zk3pQ9vX2mLw and Rb7xYq2Lk9Mn and abc"
    assert redact(text) == "pw <REDACTED:POSTGRES_PASSWORD> and <REDACTED:RABBITMQ_DEFAULT_PASS> and abc"


def test_new_secrets_env_needs_cache_clear():
    assert redact("Qw3rty-Uiop-1234") == "Qw3rty-Uiop-1234"
    path = get_settings().runs_dir / "r2" / "leaseweb" / "secrets.env"
    path.parent.mkdir(parents=True)
    path.write_text("MINIO_SECRET=Qw3rty-Uiop-1234\n", encoding="utf-8")
    assert redact("Qw3rty-Uiop-1234") == "Qw3rty-Uiop-1234"            # cached
    secret_values.cache_clear()                                          # what the pipeline does after generate
    assert redact("Qw3rty-Uiop-1234") == "<REDACTED:MINIO_SECRET>"


def test_same_name_in_two_runs_redacts_both_values():
    _secrets_env("POSTGRES_PASSWORD=first-password-1\n", "r1")
    _secrets_env("POSTGRES_PASSWORD=second-password-2\n", "r2")
    values = secret_values()
    assert set(values.values()) == {"first-password-1", "second-password-2"}
    out = redact("first-password-1 second-password-2")
    assert "first-password-1" not in out and "second-password-2" not in out


def test_longest_value_first():
    _secrets_env("A_PASS=abcdefgh\nB_PASS=abcdefgh-longer\n")
    assert redact("abcdefgh-longer") == "<REDACTED:B_PASS>"


def test_value_inside_a_placeholder_does_not_break_idempotency():
    _secrets_env("WEIRD=aws_access_key\n")                              # 14 chars, equals a placeholder name
    once = redact(f"{AWS_KEY} aws_access_key")
    assert once == "<REDACTED:aws_access_key> <REDACTED:WEIRD>"
    assert redact(once) == once


# ---------- properties ----------

SAMPLE = (f"AWS_ACCESS_KEY_ID={AWS_KEY}\nAWS_SECRET_ACCESS_KEY={AWS_SECRET}\nDB_PASSWORD=hunter2hunter2\n"
          'conn = connect(password="literal-pw")\nurl = "postgresql://u:p4ss@db/x"\n'
          "def get_token(): return token_url\n")


def test_idempotent():
    _secrets_env("POSTGRES_PASSWORD=Zk3pQ9vX2mLw\n")
    text = SAMPLE + "and Zk3pQ9vX2mLw\n"
    once = redact(text)
    assert redact(once) == once
    assert find_secrets(once) == []


def test_find_secrets_names_what_redact_replaces_without_the_values():
    found = find_secrets(SAMPLE)
    assert found == ["aws_access_key", "aws_secret", "DB_PASSWORD", "password", "userinfo"]
    assert not any(secret in " ".join(found) for secret in (AWS_KEY, AWS_SECRET, "hunter2hunter2", "literal-pw"))


def test_redact_with_map():
    text, mapping = redact_with_map(SAMPLE)
    assert text == redact(SAMPLE)
    assert mapping["<REDACTED:aws_access_key>"] == AWS_KEY
    assert mapping["<REDACTED:DB_PASSWORD>"] == "hunter2hunter2"
    assert mapping["<REDACTED:userinfo>"] == "u:p4ss"
    assert "def get_token(): return token_url" in text
