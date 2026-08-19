"""
Routes for the calendar kill switch.

The kill switch's own standalone page has been merged into the Hangar
Schedule page's "Activity Log" tab (task: hybrid kill switch + record of
past activities - see templates/calendar.html and
app.camp_extensions.kill_switch.get_hangar_activity_log). This module now
only keeps the scan-now action and the CRS watcher itself; the old page
route redirects into the merged tab for anyone with it bookmarked.
"""
from flask import Blueprint, redirect
from app.camp_extensions import kill_switch as ks
from app.auth import get_current_company_id

bp = Blueprint('kill_switch', __name__)


@bp.route('/killswitch')
def kill_switch_page():
    return redirect('/calendar#activity-log')


@bp.route('/killswitch/scan-now', methods=['POST'])
def kill_switch_scan_now():
    ks.run_kill_switch_scan(company_id=get_current_company_id())
    return redirect('/calendar#activity-log')
