"""
Tests for NotificationInstance.clearPendingEmailFlag()'s UTC/localtime
correctness (netalertx/NetAlertX#1851).

eveDateTime is always stored in UTC (utils.datetime_utils.timeNowUTC() is the
single source of truth for DB timestamps). The down_devices selection query
in notification_sections.py compares it against a plain
`datetime('now', '-N minutes')` cutoff, i.e. UTC vs UTC. clearPendingEmailFlag()
must use the same UTC-only cutoff to clear the flag it set - applying an
additional timezone-offset modifier shifts the clear cutoff by the server's
UTC offset, so west-of-UTC zones leave old 'Device Down' events flagged for
hours (repeatedly re-sent, never cleared) and east-of-UTC zones would clear
them too early.

License: GNU GPLv3
"""

import sys
import os
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db_test_helpers import (  # noqa: E402
    make_db,
    insert_device,
    minutes_ago,
    DummyDB,
    CREATE_PLUGINS_EVENTS,
)

INSTALL_PATH = os.getenv("NETALERTX_APP", "/app")
sys.path.extend([f"{INSTALL_PATH}/server"])

import conf  # noqa: E402

# json2table isn't needed by this test (only finalize_html() uses it), but
# notification_instance.py imports it at module level; it's a real
# requirements.txt dependency, normally installed. Only stub it, for the
# duration of the import below, when genuinely unavailable (e.g. a sandbox
# without the full requirements installed) - stubbing it unconditionally
# would shadow the real package for every other test file sharing this
# pytest session once models.notification_instance is cached in sys.modules.
try:
    import json2table  # noqa: F401
    _json2table_was_stubbed = False
except ImportError:
    _json2table_was_stubbed = True
    _stub = types.ModuleType("json2table")
    _stub.convert = lambda *a, **k: ""
    sys.modules["json2table"] = _stub

from models.notification_instance import NotificationInstance  # noqa: E402

if _json2table_was_stubbed:
    sys.modules.pop("json2table", None)

from messaging.reporting import get_notifications  # noqa: E402
from utils.datetime_utils import timeNowUTC, DATETIME_PATTERN  # noqa: E402


def _make_db():
    """make_db() plus Plugins_Events, which clearPendingEmailFlag() always
    clears at the end via clearPluginEvents()."""
    conn = make_db()
    conn.execute(CREATE_PLUGINS_EVENTS)
    conn.commit()
    return conn


def _insert_down_event(conn, mac, minutes_old):
    """Insert a pending 'Device Down' Events row *minutes_old* minutes ago (UTC)."""
    conn.execute(
        """
        INSERT INTO Events (eveMac, eveDateTime, eveEventType, evePendingAlertEmail)
        VALUES (?, ?, 'Device Down', 1)
        """,
        (mac, minutes_ago(minutes_old)),
    )


def _get_flag(conn, mac):
    row = conn.execute(
        "SELECT evePendingAlertEmail FROM Events WHERE eveMac=?", (mac,)
    ).fetchone()
    return row[0] if row else None


def _clear_with_tz(conn, alert_down_minutes, tz_name, selection_cutoff=None):
    """Instantiate NotificationInstance without running its real __init__
    (which needs a live settings DB/Logger) and call clearPendingEmailFlag()
    with conf.tz set to tz_name and conf.down_alert_cutoff set to
    selection_cutoff (the cutoff get_notifications() would have recorded),
    restoring both afterward. When selection_cutoff is None the cutoff is
    derived from alert_down_minutes, as get_notifications() would have."""
    original_tz = getattr(conf, "tz", None)
    had_cutoff = hasattr(conf, "down_alert_cutoff")
    original_cutoff = getattr(conf, "down_alert_cutoff", None)
    conf.tz = ZoneInfo(tz_name) if tz_name else None
    conf.down_alert_cutoff = (
        selection_cutoff if selection_cutoff is not None else minutes_ago(alert_down_minutes)
    )
    try:
        ni = NotificationInstance.__new__(NotificationInstance)
        ni.db = DummyDB(conn)
        with patch(
            "models.notification_instance.get_setting_value",
            return_value=str(alert_down_minutes),
        ):
            ni.clearPendingEmailFlag()
    finally:
        conf.tz = original_tz
        if had_cutoff:
            conf.down_alert_cutoff = original_cutoff
        else:
            del conf.down_alert_cutoff


class TestClearPendingEmailFlagTimezone(unittest.TestCase):

    def test_old_down_event_clears_west_of_utc(self):
        """
        Regression test for the UTC/tz-offset bug (#1851).

        10 minutes old, 5-minute cutoff: the down_devices selection query
        would already be resending this event every loop (UTC-only check).
        clearPendingEmailFlag() must clear it too. Under the old
        tz-offset-applied query, America/Los_Angeles (UTC-7/-8) pushes the
        clear cutoff ~7-8 hours further into the past, so a 10-minute-old
        event incorrectly stays flagged and keeps getting resent.
        """
        conn = _make_db()
        mac = "aa:bb:cc:dd:ee:01"
        insert_device(conn, mac, alert_down=1)
        _insert_down_event(conn, mac, minutes_old=10)
        conn.commit()

        _clear_with_tz(conn, alert_down_minutes=5, tz_name="America/Los_Angeles")

        self.assertEqual(
            _get_flag(conn, mac),
            0,
            "10-minute-old Device Down event with a 5-minute cutoff must be "
            "cleared regardless of server timezone.",
        )

    def test_recent_down_event_stays_pending(self):
        """A down event younger than the cutoff must stay pending (sanity
        check that the fix isn't simply disabling the time filter)."""
        conn = _make_db()
        mac = "aa:bb:cc:dd:ee:02"
        insert_device(conn, mac, alert_down=1)
        _insert_down_event(conn, mac, minutes_old=2)
        conn.commit()

        _clear_with_tz(conn, alert_down_minutes=5, tz_name="America/Los_Angeles")

        self.assertEqual(
            _get_flag(conn, mac),
            1,
            "2-minute-old Device Down event with a 5-minute cutoff must stay pending.",
        )

    def test_recent_down_event_does_not_clear_early_east_of_utc(self):
        """
        Mirror check east of UTC (#1851 notes the opposite symptom there):
        a positive tz offset shifts the clear cutoff *forward* in time
        (e.g. Asia/Kolkata's +05:30 pushes 'now - 5 minutes' to roughly
        'now + 5h25m'), which would clear almost any already-pending event
        immediately, regardless of whether NTFPRCS_alert_down_time has
        actually elapsed.
        """
        conn = _make_db()
        mac = "aa:bb:cc:dd:ee:03"
        insert_device(conn, mac, alert_down=1)
        _insert_down_event(conn, mac, minutes_old=1)
        conn.commit()

        _clear_with_tz(conn, alert_down_minutes=5, tz_name="Asia/Kolkata")

        self.assertEqual(
            _get_flag(conn, mac),
            1,
            "1-minute-old Device Down event with a 5-minute cutoff must stay "
            "pending regardless of server timezone.",
        )


class TestClearPendingEmailFlagSelectionCutoff(unittest.TestCase):
    """The clear step must reuse the cutoff the down_devices selection query
    used, not recompute eligibility against the current time: plugins run
    between selection and clear, so an event can cross the threshold in that
    gap and would otherwise be cleared without ever having been notified."""

    def test_event_crossing_cutoff_after_selection_stays_pending(self):
        """Selection ran with a 5-minute cutoff, when this 4-minute-old event
        was not yet eligible. Recomputing from 'now' with a 3-minute window
        stands in for the time that elapsed since selection: the event looks
        eligible now, but was never selected so must stay pending."""
        conn = _make_db()
        mac = "aa:bb:cc:dd:ee:04"
        insert_device(conn, mac, alert_down=1)
        _insert_down_event(conn, mac, minutes_old=4)
        conn.commit()

        _clear_with_tz(
            conn,
            alert_down_minutes=3,
            tz_name=None,
            selection_cutoff=minutes_ago(5),
        )

        self.assertEqual(
            _get_flag(conn, mac),
            1,
            "Event not eligible at selection time must not be cleared.",
        )

    def test_event_selected_before_cutoff_is_cleared(self):
        """An event older than the selection-time cutoff was selected for the
        alert, so it must be cleared."""
        conn = _make_db()
        mac = "aa:bb:cc:dd:ee:05"
        insert_device(conn, mac, alert_down=1)
        _insert_down_event(conn, mac, minutes_old=10)
        conn.commit()

        _clear_with_tz(
            conn,
            alert_down_minutes=60,
            tz_name=None,
            selection_cutoff=minutes_ago(5),
        )

        self.assertEqual(_get_flag(conn, mac), 0)

    def test_get_notifications_records_cutoff_used_in_query(self):
        """get_notifications() computes the cutoff once, records it on conf for
        the clear step and embeds the same value in the down_devices query."""
        mock_db = Mock()
        mock_json_obj = Mock()
        mock_json_obj.columnNames = []
        mock_json_obj.json = {"data": []}
        mock_db.get_table_as_json.return_value = mock_json_obj

        settings = {
            "NTFPRCS_INCLUDED_SECTIONS": ["down_devices"],
            "NTFPRCS_alert_down_time": "5",
        }
        had_cutoff = hasattr(conf, "down_alert_cutoff")
        original_cutoff = getattr(conf, "down_alert_cutoff", None)
        try:
            before = timeNowUTC(as_string=False)
            with patch(
                "messaging.reporting.get_setting_value",
                side_effect=lambda key: settings.get(key, ""),
            ):
                get_notifications(mock_db)
            after = timeNowUTC(as_string=False)

            cutoff = conf.down_alert_cutoff
            query = mock_db.get_table_as_json.call_args[0][0]
        finally:
            if had_cutoff:
                conf.down_alert_cutoff = original_cutoff
            elif hasattr(conf, "down_alert_cutoff"):
                del conf.down_alert_cutoff

        self.assertIn(f"'{cutoff}'", query)
        cutoff_dt = datetime.strptime(cutoff, DATETIME_PATTERN).replace(tzinfo=timezone.utc)
        self.assertGreaterEqual(cutoff_dt, before - timedelta(minutes=5))
        self.assertLessEqual(cutoff_dt, after - timedelta(minutes=5))


class TestAlertDownTimeBounds(unittest.TestCase):
    """An out-of-range NTFPRCS_alert_down_time must never raise out of
    get_notifications() (it runs inside the main monitoring loop)."""

    def _run_get_notifications(self, raw_minutes):
        """Run get_notifications() with the given raw setting value and return
        the recorded cutoff as a UTC datetime, restoring conf afterward."""
        mock_db = Mock()
        mock_json_obj = Mock()
        mock_json_obj.columnNames = []
        mock_json_obj.json = {"data": []}
        mock_db.get_table_as_json.return_value = mock_json_obj
        settings = {
            "NTFPRCS_INCLUDED_SECTIONS": ["down_devices"],
            "NTFPRCS_alert_down_time": raw_minutes,
        }
        original_cutoff = conf.down_alert_cutoff
        try:
            with patch(
                "messaging.reporting.get_setting_value",
                side_effect=lambda key: settings.get(key, ""),
            ):
                get_notifications(mock_db)
            cutoff = conf.down_alert_cutoff
        finally:
            conf.down_alert_cutoff = original_cutoff
        return datetime.strptime(cutoff, DATETIME_PATTERN).replace(tzinfo=timezone.utc)

    def test_huge_value_does_not_raise(self):
        """A value too large for timedelta/datetime must be bounded, not raise
        OverflowError."""
        cutoff = self._run_get_notifications("999999999999999")
        self.assertLess(cutoff, timeNowUTC(as_string=False))

    def test_negative_value_does_not_produce_future_cutoff(self):
        """A negative window must not push the cutoff into the future."""
        cutoff = self._run_get_notifications("-5")
        self.assertLessEqual(cutoff, timeNowUTC(as_string=False))


if __name__ == "__main__":
    unittest.main()
