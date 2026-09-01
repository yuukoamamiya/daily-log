import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_log.ai import _endpoint, parse_with_ai
from daily_log.config import LocalConfig
from daily_log.errors import ValidationError


class LocalConfigTest(unittest.TestCase):
    def test_onboarding_is_incomplete_until_explicitly_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LocalConfig(Path(directory) / "config.ini")
            self.assertFalse(config.public()["application"]["onboardingCompleted"])
            config.complete_onboarding()
            self.assertTrue(config.public()["application"]["onboardingCompleted"])

    def test_ini_settings_are_local_and_api_key_is_not_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LocalConfig(Path(directory) / "config.ini")
            result = config.update({
                "ai": {
                    "enabled": True,
                    "provider": "deepseek",
                    "baseUrl": "https://api.deepseek.com/v1/chat/completions",
                    "model": "deepseek-chat",
                    "apiKey": "secret-value",
                },
                "backup": {
                    "autoBackup": True, "idleSeconds": 45, "backend": "webdav",
                    "includeData": True, "includeSecrets": False,
                    "webdav": {"url": "https://dav.example.com/log", "username": "user", "password": "dav-secret"},
                },
            })
            self.assertTrue(result["ai"]["apiKeyConfigured"])
            self.assertTrue(result["backup"]["includeData"])
            self.assertFalse(result["backup"]["includeSecrets"])
            self.assertNotIn("secret-value", json.dumps(result))
            self.assertEqual(config.ai_credentials()["api_key"], "secret-value")
            self.assertIn("api_key = secret-value", config.path.read_text(encoding="utf-8"))
            self.assertNotIn("secret-value", config.portable_text())
            self.assertNotIn("dav-secret", config.portable_text())
            self.assertIn("https://dav.example.com/log", config.portable_text())
            self.assertIn("model = deepseek-chat", config.portable_text())

    def test_secrets_and_whole_backup_encryption_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LocalConfig(Path(directory) / "config.ini")
            result = config.update({
                "backup": {
                    "includeData": True,
                    "includeSecrets": True,
                    "encryptBackup": False,
                    "backend": "local",
                    "idleSeconds": 60,
                },
            })
            self.assertTrue(result["backup"]["includeSecrets"])
            self.assertFalse(result["backup"]["encryptBackup"])
            with self.assertRaisesRegex(ValidationError, "备份密码"):
                config.update({"backup": {
                    "includeData": True,
                    "includeSecrets": False,
                    "encryptBackup": True,
                    "backend": "local",
                    "idleSeconds": 60,
                }})

    def test_calendar_subscriptions_can_be_added_hidden_and_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LocalConfig(Path(directory) / "config.ini")
            self.assertEqual(config.calendar_subscriptions()[0]["name"], "中国大陆节假日")
            added = config.add_calendar_subscription("公司日历", "https://calendar.example.com/work.ics")
            self.assertTrue(added["enabled"])
            toggled = config.toggle_calendar_subscription(added["id"], False)
            self.assertFalse(toggled["enabled"])
            config.delete_calendar_subscription(added["id"])
            self.assertNotIn(added["id"], [item["id"] for item in config.calendar_subscriptions()])


class DirectAiTest(unittest.TestCase):
    def test_ai_endpoint_blocks_insecure_remote_and_private_networks(self):
        with self.assertRaisesRegex(ValidationError, "HTTPS"):
            _endpoint("http://api.example.com/v1/chat/completions")
        with self.assertRaisesRegex(ValidationError, "私有网络"):
            _endpoint("https://169.254.169.254/latest/meta-data")
        self.assertEqual(_endpoint("http://127.0.0.1:11434"), "http://127.0.0.1:11434/v1/chat/completions")
    def test_ai_returns_normalized_preview_without_writing(self):
        response = {
            "choices": [{"message": {"content": json.dumps({
                "journal": [],
                "transactions": [{
                    "date": "2026-08-29", "summary": "午饭", "note": "", "amount": 20,
                    "account": "expenses:饮食",
                }],
                "todos": [], "calendar": [], "clarifications": [],
            }, ensure_ascii=False)}}]
        }

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        settings = {
            "enabled": True,
            "base_url": "https://api.deepseek.com/v1/chat/completions",
            "model": "deepseek-chat",
            "api_key": "test-key",
        }
        with patch("daily_log.ai.urllib.request.urlopen", return_value=FakeResponse(json.dumps(response).encode())):
            plan = parse_with_ai("午饭花了20", settings)
        self.assertEqual(plan["transactions"][0]["amount"], "20.00")

    def test_ai_requires_explicit_local_configuration(self):
        with self.assertRaisesRegex(ValidationError, "启用 AI"):
            parse_with_ai("测试", {"enabled": False})

    def test_ai_connection_probe_is_minimal_and_does_not_return_key(self):
        from daily_log.ai import test_ai_connection

        class FakeResponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        with patch("daily_log.ai.urllib.request.urlopen", return_value=FakeResponse(b"{}")) as urlopen:
            result = test_ai_connection({
                "base_url": "https://api.example.com/v1/chat/completions",
                "model": "test-model",
                "api_key": "secret-key",
            })
        self.assertTrue(result["ok"])
        request = urlopen.call_args.args[0]
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")


if __name__ == "__main__":
    unittest.main()
