import os
import tempfile

os.environ['CAMP_DATABASE_PATH'] = os.path.join(tempfile.gettempdir(), 'camp_test_import.db')

if os.path.exists(os.environ['CAMP_DATABASE_PATH']):
    os.remove(os.environ['CAMP_DATABASE_PATH'])

from app.camp_extensions import kill_switch, schedule_lifecycle

kill_switch._stop_event.set()
schedule_lifecycle._stop_event.set()