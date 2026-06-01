"""
Digital tool crib and calibration management routes for C.O.R.E. CAMP.
Tracks tools, calibration status, and checkout history.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from datetime import datetime
from app.database import get_db

bp = Blueprint('tool_crib', __name__)


@bp.route('/tool_crib')
def tool_crib():
    """Display tool crib inventory with calibration status."""
    with get_db() as conn:
        raw_tools = conn.execute('SELECT * FROM ToolCrib').fetchall()
        
        try:
            engineers = conn.execute('SELECT * FROM Engineers').fetchall()
        except Exception:
            engineers = []
    
    # Process tools and calculate calibration status
    tools = []
    categories = set()
    
    for t in raw_tools:
        categories.add(t['category'])
        
        if t['calibration_due'] == 'N/A' or not t['calibration_due']:
            cal_days = 9999
            cal_due_text = 'N/A'
        else:
            try:
                cal_days = (datetime.strptime(t['calibration_due'], '%Y-%m-%d') - datetime.now()).days
                cal_due_text = t['calibration_due']
            except (ValueError, TypeError):
                cal_days = 9999
                cal_due_text = 'Invalid Date'
        
        tools.append({
            'tool_id': t['tool_id'],
            'tool_name': t['tool_name'],
            'category': t['category'],
            'calibration_due': cal_due_text,
            'status': t['status'],
            'checked_out_to': t['checked_out_to'],
            'cal_days_remaining': cal_days
        })
    
    sorted_categories = sorted(list(categories))
    return render_template('tool_crib.html', tools=tools, categories=sorted_categories, engineers=engineers)


@bp.route('/checkout_tool/<tool_id>', methods=['POST'])
def checkout_tool(tool_id):
    """Check out a tool to an engineer."""
    emp_id = request.form.get('engineer_id')
    
    with get_db() as conn:
        engineer = conn.execute(
            'SELECT full_name FROM Engineers WHERE emp_id = ?',
            (emp_id,)
        ).fetchone()
        
        eng_name = engineer['full_name'] if engineer else "Unknown Engineer"
        
        conn.execute(
            'UPDATE ToolCrib SET status = "Checked Out", checked_out_to = ? WHERE tool_id = ?',
            (eng_name, tool_id)
        )
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))


@bp.route('/checkin_tool/<tool_id>', methods=['POST'])
def checkin_tool(tool_id):
    """Check in a tool."""
    with get_db() as conn:
        conn.execute(
            'UPDATE ToolCrib SET status = "Available", checked_out_to = "" WHERE tool_id = ?',
            (tool_id,)
        )
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))


@bp.route('/add_tool', methods=['POST'])
def add_tool():
    """Add a new tool to the crib."""
    with get_db() as conn:
        conn.execute(
            'INSERT INTO ToolCrib (tool_id, tool_name, category, calibration_due, status) '
            'VALUES (?, ?, ?, ?, "Available")',
            (
                request.form['tool_id'],
                request.form['tool_name'],
                request.form['category'],
                request.form['calibration_due']
            )
        )
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))


@bp.route('/remove_tool/<tool_id>', methods=['POST'])
def remove_tool(tool_id):
    """Remove a tool from the crib."""
    with get_db() as conn:
        conn.execute('DELETE FROM ToolCrib WHERE tool_id = ?', (tool_id,))
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))


@bp.route('/quarantine_tool/<tool_id>', methods=['POST'])
def quarantine_tool(tool_id):
    """Mark a tool as quarantined."""
    with get_db() as conn:
        conn.execute(
            'UPDATE ToolCrib SET status = "Quarantined" WHERE tool_id = ?',
            (tool_id,)
        )
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))


@bp.route('/update_tool/<tool_id>', methods=['POST'])
def update_tool(tool_id):
    """Update tool calibration date."""
    new_date = request.form['new_cal_date']
    
    with get_db() as conn:
        conn.execute(
            'UPDATE ToolCrib SET calibration_due = ?, status = "Available" WHERE tool_id = ?',
            (new_date, tool_id)
        )
        conn.commit()
    
    return redirect(url_for('tool_crib.tool_crib'))
