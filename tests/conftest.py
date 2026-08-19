import os
import tempfile

import pytest

os.environ['CAMP_DATABASE_PATH'] = os.path.join(tempfile.gettempdir(), 'camp_test_import.db')

if os.path.exists(os.environ['CAMP_DATABASE_PATH']):
    os.remove(os.environ['CAMP_DATABASE_PATH'])

from app.camp_extensions import kill_switch, schedule_lifecycle

kill_switch._stop_event.set()
schedule_lifecycle._stop_event.set()


@pytest.fixture(autouse=True)
def _background_watchers_stopped():
    """Keep the kill-switch / lifecycle watchers from running during tests.

    They poll on timers and their scans read Config.DATABASE_PATH at runtime,
    so a thread started by an earlier test could land on a later test's
    database. start_watcher() also refuses to restart once the stop events
    are set, so tests never spin them up at all.
    """
    kill_switch._stop_event.set()
    schedule_lifecycle._stop_event.set()
    yield