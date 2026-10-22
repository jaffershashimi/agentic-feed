import unittest
from datetime import date, datetime, timezone

from feed_pipeline import calendar


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class CalendarTest(unittest.TestCase):
    def test_sessions_skip_weekends_and_holidays(self):
        self.assertTrue(calendar.is_session(date(2026, 10, 22)))
        self.assertFalse(calendar.is_session(date(2026, 10, 24)))
        self.assertFalse(calendar.is_session(date(2026, 11, 26)))
        self.assertEqual(calendar.previous_session(date(2026, 11, 27)), date(2026, 11, 25))

    def test_last_completed_session_respects_close_and_weekends(self):
        # 2026-10-22 is a Thursday. 19:59 UTC is 15:59 ET (before close), 20:00 UTC is the close.
        self.assertEqual(calendar.last_completed_session(utc(2026, 10, 22, 19, 59)), date(2026, 10, 21))
        self.assertEqual(calendar.last_completed_session(utc(2026, 10, 22, 20, 0)), date(2026, 10, 22))
        # Monday 05:52 PT reads Friday's session.
        self.assertEqual(calendar.last_completed_session(utc(2026, 10, 26, 12, 52)), date(2026, 10, 23))
        # Day after Thanksgiving closes early at 13:00 ET (18:00 UTC in November).
        self.assertEqual(calendar.last_completed_session(utc(2026, 11, 27, 18, 1)), date(2026, 11, 27))
        self.assertEqual(calendar.last_completed_session(utc(2026, 11, 27, 17, 59)), date(2026, 11, 25))

    def test_daylight_saving_shift(self):
        # After the Nov 1, 2026 switch, the 16:00 ET close is 21:00 UTC.
        self.assertEqual(calendar.last_completed_session(utc(2026, 11, 2, 20, 30)), date(2026, 10, 30))
        self.assertEqual(calendar.last_completed_session(utc(2026, 11, 2, 21, 0)), date(2026, 11, 2))

    def test_opex_and_events(self):
        self.assertEqual(calendar.monthly_opex(2026, 10), date(2026, 10, 16))
        self.assertEqual(calendar.monthly_opex(2027, 3), date(2027, 3, 19))
        types = {(e["date"], e["type"]) for e in calendar.upcoming_events(date(2026, 10, 22), 10)}
        self.assertIn(("2026-10-28", "fomc_statement"), types)
        types = {(e["date"], e["type"]) for e in calendar.upcoming_events(date(2026, 11, 20), 8)}
        self.assertIn(("2026-11-20", "monthly_opex"), types)
        self.assertIn(("2026-11-26", "market_closed"), types)
        self.assertIn(("2026-11-27", "early_close"), types)

    def test_uncovered_year_fails_loudly(self):
        with self.assertRaises(ValueError):
            calendar.is_session(date(2028, 1, 3))
        self.assertFalse(calendar.is_session(date(2025, 1, 9)))


if __name__ == "__main__":
    unittest.main()
