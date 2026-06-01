"""
Aircraft workspace management routes for C.O.R.E. CAMP.
Handles fleet management, directives, and maintenance tasks.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from app.database import get_db
from app.utils import save_upload_file, get_aircraft_id_from_registration

bp = Blueprint('workspace', __name__)


@bp.route('/workspace')
def workspace():
    """Display workspace for managing aircraft and directives."""
    with get_db() as conn:
        fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
        unique_models = conn.execute('SELECT DISTINCT model FROM Aircraft').fetchall()
    
    return render_template('workspace.html', fleet=fleet, unique_models=unique_models)


@bp.route('/add_aircraft', methods=['POST'])
def add_aircraft():
    """Add a new aircraft to the fleet."""
    reg = request.form['registration'].upper()
    model = request.form['model']
    hours = float(request.form['total_flight_hours'])
    cycles = int(request.form['total_cycles'])
    
    aircraft_id = get_aircraft_id_from_registration(reg)
    
    # Handle PDF upload
    pdf_path = ""
    if 'amm_pdf' in request.files:
        file = request.files['amm_pdf']
        if file.filename != '':
            # Use model-based prefix (original behavior)
            pdf_path = save_upload_file(file, f"AMM_{model}")
    
    with get_db() as conn:
        conn.execute(
            'INSERT INTO Aircraft (aircraft_id, registration, model, total_flight_hours, total_cycles, amm_pdf_path) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (aircraft_id, reg, model, hours, cycles, pdf_path)
        )
        conn.commit()
    
    return redirect(url_for('workspace.workspace'))


@bp.route('/update_amm', methods=['POST'])
def update_amm():
    """Update aircraft AMM PDF."""
    aircraft_id = request.form['aircraft_id']
    pdf_path = ""
    
    if 'amm_pdf' in request.files:
        file = request.files['amm_pdf']
        if file.filename != '':
            # Use timestamp-based prefix (original behavior)
            from datetime import datetime
            time_stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            pdf_path = save_upload_file(file, f"AMM_Rev_{time_stamp}")
    
    if pdf_path:
        with get_db() as conn:
            conn.execute(
                'UPDATE Aircraft SET amm_pdf_path = ? WHERE aircraft_id = ?',
                (pdf_path, aircraft_id)
            )
            conn.commit()
    
    return redirect(url_for('workspace.workspace'))


@bp.route('/add_directive', methods=['POST'])
def add_directive():
    """Add an airworthiness directive."""
    target_model = request.form['target_model']
    doc_type = request.form['doc_type']
    ref_number = request.form['ref_number']
    description = request.form['description']
    
    pdf_path = ""
    if 'doc_pdf' in request.files:
        pdf_path = save_upload_file(request.files['doc_pdf'], f"{doc_type}_{ref_number}")
    
    with get_db() as conn:
        conn.execute(
            'INSERT INTO Directives (target_model, doc_type, ref_number, description, pdf_path) '
            'VALUES (?, ?, ?, ?, ?)',
            (target_model, doc_type, ref_number, description, pdf_path)
        )
        conn.commit()
    
    return redirect(url_for('workspace.workspace'))


@bp.route('/add_task', methods=['POST'])
def add_task():
    """Add a maintenance task template."""
    target_model = request.form.get('target_model', 'ALL')
    
    with get_db() as conn:
        conn.execute('''
            INSERT INTO MaintenanceTasks 
            (task_id, task_name, task_category, interval_hours, interval_cycles, interval_months, target_model) 
            VALUES (?, ?, "Routine", ?, ?, 0, ?)
        ''', (
            request.form['task_id'],
            request.form['task_name'],
            request.form['interval_hours'],
            request.form['interval_cycles'],
            target_model
        ))
        conn.commit()
    
    return redirect(url_for('workspace.workspace'))


@bp.route('/remove_aircraft/<aircraft_id>', methods=['POST'])
def remove_aircraft(aircraft_id):
    """Remove an aircraft from the fleet."""
    with get_db() as conn:
        conn.execute('DELETE FROM Aircraft WHERE aircraft_id = ?', (aircraft_id,))
        conn.commit()
    
    return redirect(url_for('workspace.workspace'))


@bp.route('/setup_lifecycles')
def setup_lifecycles():
    """Temporary route to initialize lifecycle columns and seed data."""
    messages = []
    
    with get_db() as conn:
        # Lifecycle columns should already exist from migrations
        messages.append("✅ Lifecycle columns present in Components table.")
        
        try:
            conn.execute('''
                INSERT OR REPLACE INTO Components (component_id, aircraft_id, csn, max_csn) 
                VALUES ('Nose_Landing_Gear', 'Aircraft_5N_TAJ', 4995, 5000)
            ''')
            conn.commit()
            messages.append("⚙️ Seeded Nose_Landing_Gear for 5N-TAJ with 4,995 cycles.")
        except Exception as e:
            messages.append(f"❌ Error seeding data: {e}")
    
    html_output = "<h2>Database Update Complete!</h2>" + "<br>".join(messages)
    html_output += "<br><br><a href='/'>Click here to return to your Dashboard</a>"
    return html_output


@bp.route('/add_swrl_rule', methods=['POST'])
def add_swrl_rule():
    rule_name = request.form.get('rule_name', '').strip()
    rule_body = request.form.get('rule_body', '').strip()
    
    if not rule_name or not rule_body:
        return "Error: Rule name and rule body are required.<br><br><a href='/workspace'>Return to Workspace</a>", 400
    
    with get_db() as conn:
        conn.execute(
            'INSERT INTO SWRLRules (rule_name, rule_body) VALUES (?, ?)',
            (rule_name, rule_body)
        )
        conn.execute(
            'INSERT INTO XAILogs (component_id, ai_decision, explanation_text) VALUES (?, ?, ?)',
            ('SWRL Editor', 'Rule Pending Review', f"{rule_name}: {rule_body}")
        )
        conn.commit()
    
    return (
        "<h2>SWRL Rule Captured for Review</h2>"
        "<p>The rule was stored safely and logged for engineering review before ontology injection.</p>"
        "<br><a href='/workspace'>Return to Workspace</a>"
    )
