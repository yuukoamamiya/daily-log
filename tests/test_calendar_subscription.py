import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_log.calendar_subscription import load_subscription_events, refresh_subscription


ICS = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:holiday-1\r
SUMMARY:\xe5\x9b\xbd\xe5\xba\x86\xe8\x8a\x82\r
DTSTART;VALUE=DATE:20261001\r
DTEND;VALUE=DATE:20261008\r
END:VEVENT\r
END:VCALENDAR\r
"""


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class CalendarSubscriptionTest(unittest.TestCase):
    def test_refresh_caches_read_only_events(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "holidays.ics"
            with (
                patch("daily_log.calendar_subscription.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]),
                patch("daily_log.calendar_subscription.urllib.request.build_opener") as build_opener,
            ):
                build_opener.return_value.open.return_value = FakeResponse(ICS)
                result = refresh_subscription("https://calendar.example.com/china.ics", cache)
            events = load_subscription_events(cache)
            self.assertEqual(result["count"], 1)
            self.assertEqual(events[0]["title"], "国庆节")
            self.assertTrue(events[0]["readOnly"])
            self.assertEqual(events[0]["source"], "subscription")


if __name__ == "__main__":
    unittest.main()
