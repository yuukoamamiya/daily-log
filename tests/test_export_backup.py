import io
import json
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from daily_log.backup_archive import (
    decrypt_archive,
    decrypt_secrets,
    encrypt_archive,
    encrypt_secrets,
    restore_backup,
)
from daily_log.cloud_backup import (
    download_latest_archive,
    download_latest_s3,
    download_latest_webdav,
    upload_s3,
    upload_webdav,
    test_s3_connection,
    test_webdav_connection,
)
from daily_log.config import LocalConfig
from daily_log.errors import ValidationError
from daily_log.database import DailyLogDatabase
from daily_log.exporter import create_portable_archive, export_data_file


class FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ExportBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = DailyLogDatabase(self.root / "state.db")
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-30", "summary": "午饭", "amount": "20", "account": "expenses:饮食", "note": ""}],
            "journal": [{"date": "2026-08-30", "text": "原样日记", "tags": ["生活"]}],
            "todos": [{"created_date": "2026-08-30", "text": "写文章", "tags": ["写作"]}],
            "calendar": [{"date": "2026-08-31", "title": "开会", "start_time": "10:00", "end_time": "11:00", "location": "", "description": ""}],
            "clarifications": [],
        }, "2026-08-30")

    def tearDown(self):
        self.temp.cleanup()

    def test_portable_archive_contains_interoperable_formats(self):
        archive = create_portable_archive(self.database, destination=self.root, include_database=True, settings_text="[backup]\nbackend = local\n")
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            self.assertIn("todo/todo.txt", names)
            self.assertIn("diary/2026-08-30.md", names)
            self.assertIn("ledger.csv", names)
            self.assertIn("calendar.ics", names)
            self.assertIn("daily-log.org", names)
            self.assertIn("categories.txt", names)
            self.assertIn("daily-log.db", names)
            self.assertIn("config.portable.ini", names)
            self.assertIn("backup-manifest.json", names)

    def test_each_interoperable_format_exports_as_one_file(self):
        expected = {
            "expenses-csv": (".csv", "午饭"),
            "diary-markdown": (".md", "原样日记"),
            "todo-txt": (".txt", "写文章"),
            "calendar-ics": (".ics", "BEGIN:VCALENDAR"),
            "org": (".org", "#+TITLE: Daily Log"),
        }
        for export_format, (suffix, content) in expected.items():
            with self.subTest(export_format=export_format):
                path = export_data_file(self.database, export_format, self.root / "exports")
                self.assertEqual(path.suffix, suffix)
                if suffix == ".ics":
                    self.assertIn(content.encode(), path.read_bytes())
                else:
                    self.assertIn(content, path.read_text(encoding="utf-8-sig"))

    def test_credentials_are_authenticated_and_password_encrypted(self):
        secrets = {"ai": {"api_key": "deep-secret"}}
        blob = encrypt_secrets(secrets, "long-password")
        self.assertNotIn(b"deep-secret", blob)
        self.assertEqual(decrypt_secrets(blob, "long-password"), secrets)
        with self.assertRaisesRegex(ValidationError, "密码错误"):
            decrypt_secrets(blob, "wrong-password")

    def test_plaintext_secret_backup_and_whole_archive_encryption_are_separate(self):
        config = LocalConfig(self.root / "config.ini")
        secrets = {"ai": {"api_key": "plain-secret"}, "webdav": {}, "s3": {}}
        archive = create_portable_archive(
            self.database,
            destination=self.root,
            include_database=False,
            include_portable=False,
            secrets_text=json.dumps(secrets),
        )
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(json.loads(bundle.read("secrets.json")), secrets)
        encrypted = encrypt_archive(archive, "whole-backup-password", remove_source=True)
        self.assertFalse(archive.exists())
        self.assertNotIn(b"plain-secret", encrypted.read_bytes())
        decrypted = decrypt_archive(encrypted, "whole-backup-password", self.root / "decrypted.zip")
        details = restore_backup(decrypted, self.database, config)
        self.assertTrue(details["includesSecrets"])
        self.assertEqual(config.ai_credentials()["api_key"], "plain-secret")

    def test_local_restore_replaces_database_without_repository_files(self):
        config = LocalConfig(self.root / "config.ini")
        portable = self.root / "portable"
        (portable / "data" / "diary").mkdir(parents=True)
        (portable / "data" / "diary" / "journal.txt").write_text("snapshot projection", encoding="utf-8")
        self.database.set_monthly_budget("4321")
        archive = create_portable_archive(
            self.database,
            destination=self.root / "backups",
            include_database=True,
            settings_text=config.portable_text(),
            portable_root=portable,
        )
        self.database.apply_plan({
            "transactions": [{"date": "2026-08-30", "summary": "后来新增", "amount": "1", "account": "expenses:测试", "note": ""}],
            "journal": [], "todos": [], "calendar": [], "clarifications": [],
        }, "2026-08-30")
        self.database.set_monthly_budget("1")
        (portable / "data" / "diary" / "journal.txt").write_text("changed projection", encoding="utf-8")
        details = restore_backup(archive, self.database, config, portable)
        self.assertTrue(details["includesData"])
        self.assertNotIn("后来新增", [item["summary"] for item in self.database.list_transactions()])
        self.assertEqual(self.database.get_monthly_budget(), 4321.0)
        self.assertFalse((self.root / "README.md").exists())
        self.assertFalse((self.root / "repository").exists())
        self.assertEqual(
            (portable / "data" / "diary" / "journal.txt").read_text(encoding="utf-8"),
            "snapshot projection",
        )
        self.assertEqual(download_latest_archive({"backend": "local"}, self.root / "backups"), archive)

    def test_webdav_put_and_s3_signature(self):
        archive = create_portable_archive(self.database, destination=self.root)
        requests = []

        def capture(request, timeout):
            requests.append(request)
            return FakeResponse()

        with patch("daily_log.cloud_backup.urllib.request.urlopen", side_effect=capture):
            upload_webdav(archive, {"url": "https://dav.example.com/log", "username": "u", "password": "p"})
            upload_s3(archive, {
                "endpoint": "https://s3.example.com", "region": "us-east-1", "bucket": "backup",
                "prefix": "daily-log", "access_key": "access", "secret_key": "secret",
            })
        self.assertEqual(requests[0].method, "PUT")
        self.assertTrue(requests[0].get_header("Authorization").startswith("Basic "))
        self.assertEqual(requests[1].method, "PUT")
        self.assertTrue(requests[1].get_header("Authorization").startswith("AWS4-HMAC-SHA256 "))

    def test_custom_proxy_is_used_and_transient_connection_errors_are_retried(self):
        archive = create_portable_archive(self.database, destination=self.root)
        opener = Mock()
        opener.open.side_effect = [urllib.error.URLError("temporary"), FakeResponse()]
        with patch("daily_log.cloud_backup.urllib.request.build_opener", return_value=opener) as build_opener:
            upload_webdav(archive, {
                "url": "https://dav.example.com/log", "username": "u", "password": "p",
                "proxy": {"mode": "custom", "url": "http://127.0.0.1:7890", "username": "pu", "password": "pp"},
            })
        self.assertEqual(opener.open.call_count, 2)
        proxy_handler = build_opener.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
        self.assertEqual(len(build_opener.call_args.args), 2)

    def test_cloud_backends_reject_private_network_targets(self):
        archive = create_portable_archive(self.database, destination=self.root)
        with self.assertRaisesRegex(ValidationError, "私有网络"):
            upload_webdav(archive, {"url": "https://169.254.169.254/dav"})
        with self.assertRaisesRegex(ValidationError, "私有网络"):
            upload_s3(archive, {
                "endpoint": "https://10.0.0.1", "region": "us-east-1", "bucket": "backup",
                "prefix": "", "access_key": "a", "secret_key": "s",
            })

    def test_cloud_connection_probes_do_not_upload_and_use_clear_errors(self):
        class ProbeResponse(io.BytesIO):
            status = 207

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        requests = []

        def capture(request, timeout):
            requests.append(request)
            return ProbeResponse()

        with patch("daily_log.cloud_backup.urllib.request.urlopen", side_effect=capture):
            self.assertTrue(test_webdav_connection({
                "url": "https://dav.example.com/dav", "username": "u", "password": "dav-secret",
            })["ok"])
            self.assertTrue(test_s3_connection({
                "endpoint": "https://s3.example.com", "region": "us-east-1", "bucket": "backup",
                "access_key": "access", "secret_key": "secret",
            })["ok"])
        self.assertEqual(requests[0].method, "PROPFIND")
        self.assertEqual(requests[1].method, "GET")
        self.assertNotIn("dav-secret", requests[0].data.decode("utf-8"))

    def test_webdav_and_s3_can_download_the_latest_archive(self):
        listing = b'''<?xml version="1.0"?><multistatus xmlns="DAV:"><response><href>/dav/daily-log-20260830-010000-000001.zip</href></response></multistatus>'''
        s3_listing = b'''<?xml version="1.0"?><ListBucketResult><Contents><Key>daily-log/daily-log-20260830-010000-000002.zip</Key></Contents></ListBucketResult>'''
        webdav_settings = {"url": "https://dav.example.com/dav", "username": "u", "password": "p"}
        s3_settings = {
            "endpoint": "https://s3.example.com", "region": "us-east-1", "bucket": "backup",
            "prefix": "daily-log", "access_key": "access", "secret_key": "secret",
        }
        with patch("daily_log.cloud_backup.urllib.request.urlopen", side_effect=[FakeResponse(listing), FakeResponse(b"webdav-zip")]):
            webdav = download_latest_webdav(webdav_settings, self.root / "webdav")
        with patch("daily_log.cloud_backup.urllib.request.urlopen", side_effect=[FakeResponse(s3_listing), FakeResponse(b"s3-zip")]):
            s3 = download_latest_s3(s3_settings, self.root / "s3")
        self.assertEqual(webdav.read_bytes(), b"webdav-zip")
        self.assertEqual(s3.read_bytes(), b"s3-zip")


if __name__ == "__main__":
    unittest.main()
