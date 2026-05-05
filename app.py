from owlready2 import *
from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import json
import os
import re
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)

# Configure the upload folder for PDFs
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 

# --- SELF-HEALING DATABASE CONNECTION ---
def get_db_connection():
    # Tells SQLite to wait up to 10 seconds for the door to unlock instead of crashing
    conn = sqlite3.connect('camp_system.db', timeout=10.0)
    conn.row_factory = sqlite3.Row 
    
    try:
        conn.execute("ALTER TABLE Faults ADD COLUMN resolved_by TEXT")
    except sqlite3.OperationalError:
        pass 
        
    try:
        conn.execute("ALTER TABLE Faults ADD COLUMN resolved_date TEXT")
    except sqlite3.OperationalError:
        pass 
        
    try:
        conn.execute("ALTER TABLE MaintenanceHistory ADD COLUMN completion_date DATETIME DEFAULT CURRENT_TIMESTAMP")
    except sqlite3.OperationalError:
        pass 
    try:
        conn.execute("ALTER TABLE MaintenanceTasks ADD COLUMN target_model TEXT DEFAULT 'ALL'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS CRS_Records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aircraft_reg TEXT,
            reference_id TEXT,
            description TEXT,
            signed_off_by TEXT,
            release_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
    except sqlite3.OperationalError:
        pass
    return conn

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --- CASE-BASED REASONING (CBR) SEMANTIC ENGINE ---
def retrieve_similar_cases(current_fault_desc, aircraft_reg, threshold=0.3):
    conn = get_db_connection()
    # Fetch the "Case Base" (Retain Phase of CBR)
    historical_cases = conn.execute('''
        SELECT task_description, signed_off_by, completion_date 
        FROM MaintenanceHistory 
        WHERE aircraft_reg = ?
    ''', (aircraft_reg,)).fetchall()
    conn.close()

    if not historical_cases:
        return []

    # Extract text corpus for NLP math
    case_texts = [case['task_description'] for case in historical_cases]
    case_texts.insert(0, current_fault_desc) # Put the current fault at index 0

    # Calculate Semantic Similarity using TF-IDF & Cosine Similarity
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(case_texts)
    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()

    # Retrieve matching cases above the similarity threshold
    similar_cases = []
    for idx, score in enumerate(cosine_sim):
        if score >= threshold:
            similar_cases.append({
                'similarity_score': round(score * 100, 1), # Convert to percentage
                'task_description': historical_cases[idx]['task_description'],
                'mechanic': historical_cases[idx]['signed_off_by'],
                'date': historical_cases[idx]['completion_date']
            })
            
    # Sort by highest similarity match
    similar_cases.sort(key=lambda x: x['similarity_score'], reverse=True)
    return similar_cases[:3] # Return top 3 historical precedents

# --- 1. DASHBOARD (Model-Aware) ---
@app.route('/')
def dashboard():
    conn = get_db_connection()
    fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
    
    selected_tail = request.args.get('tail')
    if not selected_tail and fleet: selected_tail = fleet[0]['aircraft_id']
    
    selected_aircraft = next((plane for plane in fleet if plane['aircraft_id'] == selected_tail), fleet[0])
    current_model = selected_aircraft['model']
    
    directives = conn.execute('SELECT * FROM Directives WHERE target_model = ? AND status = "Open" ORDER BY doc_type ASC', (current_model,)).fetchall()
    raw_faults = conn.execute('SELECT f.fault_id, f.fault_type, f.severity, c.component_id, c.aircraft_id FROM Faults f JOIN Components c ON f.component_id = c.component_id WHERE f.resolved = 0 AND c.aircraft_id = ?', (selected_tail,)).fetchall()
    
    # Run the CBR Engine for every active fault
    faults_with_cbr = []
    for f in raw_faults:
        fault_dict = dict(f)
        ac_reg = f['aircraft_id'].replace('Aircraft_', '')
        
        # Call the Retrieve phase of CBR
        similar_past_repairs = retrieve_similar_cases(f['fault_type'], ac_reg)
        fault_dict['cbr_matches'] = similar_past_repairs
        faults_with_cbr.append(fault_dict)
    telemetry = conn.execute('SELECT t.sensor_type, t.reading_value, t.recorded_at, c.component_id FROM SensorTelemetry t JOIN Components c ON t.component_id = c.component_id WHERE c.aircraft_id = ? ORDER BY t.recorded_at DESC LIMIT 10', (selected_tail,)).fetchall()
    schedule_sync = conn.execute('SELECT * FROM Schedule ORDER BY start_time ASC LIMIT 6').fetchall()
    
    chart_labels = [plane['registration'] for plane in fleet]
    chart_data = [plane['total_flight_hours'] for plane in fleet]
    
    try:
        engineers = conn.execute('SELECT * FROM Engineers').fetchall()
    except sqlite3.OperationalError:
        engineers = []
        
    conn.close()
    
    return render_template('dashboard.html', fleet=fleet, selected_aircraft=selected_aircraft, faults=faults_with_cbr, telemetry=telemetry, directives=directives, schedule_sync=schedule_sync, chart_labels=json.dumps(chart_labels), chart_data=json.dumps(chart_data), engineers=engineers)
# --- ONTOLOGY-DRIVEN FAULT RESOLUTION ---
@app.route('/resolve_fault/<int:fault_id>', methods=['POST'])
def resolve_fault(fault_id):
    mechanic_id = request.form.get('mechanic_id')
    
    conn = get_db_connection()
    fault = conn.execute('SELECT * FROM Faults WHERE fault_id = ?', (fault_id,)).fetchone()
    mechanic = conn.execute('SELECT * FROM Engineers WHERE emp_id = ?', (mechanic_id,)).fetchone()
    
    if not fault or not mechanic:
        conn.close()
        return "Error: Fault or Mechanic not found.", 400

    # 1. AI Ontology Compliance Check
    onto_path.append(".")
    
    # THE MOA UPGRADE: Load the base ontology first, then the 7-layer MOA
    base_onto = get_ontology("camp.owl").load()
    onto = get_ontology("camp_multi_ontology.owl").load()
    
    required_license = "None"
    amm_chapter = fault['amm_reference'].split(" ")[0]
    
    with onto:
        if hasattr(onto, amm_chapter):
            chapter_class = getattr(onto, amm_chapter)
            if chapter_class is not None and hasattr(chapter_class, 'requiresLicense'):
                if chapter_class.requiresLicense:
                    required_license = chapter_class.requiresLicense[0].name
                
    mechanic_license = mechanic['license_type']
    if required_license != "None" and required_license != mechanic_license:
        conn.close()
        return f"<h1>COMPLIANCE LOCKOUT</h1><p>The ontology requires a <b>{required_license}</b> to sign off on {amm_chapter}. You hold a {mechanic_license}.</p><a href='/'>Return to Dashboard</a>", 403

    # 2. Build the Official Digital Signature
    digital_signature = f"{mechanic['full_name']} (Lic: {mechanic_license} | Stamp: {mechanic['stamp_number']})"

    # 3. Mark Fault as Resolved
    conn.execute('''
        UPDATE Faults 
        SET resolved = 1, resolved_by = ?, resolved_date = datetime('now', 'localtime') 
        WHERE fault_id = ?
    ''', (digital_signature, fault_id))
    
    # 4. Auto-Close the PIREP
    if fault['amm_reference'].startswith("PIREP_ID_"):
        exact_pirep_id = fault['amm_reference'].split("_")[-1]
        try:
            conn.execute("UPDATE PilotReports SET status = 'Closed' WHERE rowid = ?", (exact_pirep_id,))
        except sqlite3.OperationalError:
            pass
            
    # 5. Locate the Aircraft Registration
    comp = conn.execute('SELECT aircraft_id FROM Components WHERE component_id = ?', (fault['component_id'],)).fetchone()
    ac_reg = comp['aircraft_id'].replace('Aircraft_', '') if comp else "UNKNOWN"
    
    # 6. Generate the Legal Certificate of Release to Service (CRS)
    conn.execute('''
        INSERT INTO CRS_Records (aircraft_reg, reference_id, description, signed_off_by) 
        VALUES (?, ?, ?, ?)
    ''', (ac_reg, f"FAULT-{fault_id}", f"Cleared {fault['fault_type']}", digital_signature))
    
    # 7. NEW FIX: Send a carbon copy to the Global Maintenance History Log!
    conn.execute('''
        INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
        VALUES (?, ?, ?)
    ''', (ac_reg, f"Resolved Fault: {fault['fault_type']}", digital_signature))
    
    # 8. DIGITAL TWIN SENSOR RESET: Simulate the completed repair
    if "Overheat" in fault['fault_type']:
        conn.execute("INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Thermocouple', 450.0)", (fault['component_id'],))
    elif "Vibration" in fault['fault_type']:
        conn.execute("INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Vibration Sensor', 1.2)", (fault['component_id'],))
    elif "Leak" in fault['fault_type'] or "Pressure" in fault['fault_type']:
        conn.execute("INSERT INTO SensorTelemetry (component_id, sensor_type, reading_value) VALUES (?, 'Pressure Sensor', 45.0)", (fault['component_id'],))

    conn.commit()
    conn.close()
    
    target_tail = comp['aircraft_id'] if comp else None
    return redirect(url_for('dashboard', tail=target_tail))

# --- TEMPORARY DATABASE SETUP ROUTE ---
@app.route('/setup_lifecycles')
def setup_lifecycles():
    conn = get_db_connection()
    messages = []

    try:
        conn.execute('ALTER TABLE Components ADD COLUMN csn INTEGER DEFAULT 0')
        conn.execute('ALTER TABLE Components ADD COLUMN max_csn INTEGER DEFAULT 5000')
        messages.append("✅ Lifecycle columns added to Components table.")
    except sqlite3.OperationalError:
        messages.append("✅ Lifecycle columns already exist.")

    try:
        conn.execute('''
            INSERT OR REPLACE INTO Components (component_id, aircraft_id, csn, max_csn) 
            VALUES ('Nose_Landing_Gear', 'Aircraft_5N_TAJ', 4995, 5000)
        ''')
        conn.commit()
        messages.append("⚙️ Seeded Nose_Landing_Gear for 5N-TAJ with 4,995 cycles.")
    except Exception as e:
        messages.append(f"❌ Error seeding data: {e}")

    conn.close()
    html_output = "<h2>Database Update Complete!</h2>" + "<br>".join(messages)
    html_output += "<br><br><a href='/'>Click here to return to your Dashboard</a>"
    return html_output

# --- CAMO WORKSPACE ---
@app.route('/workspace')
def workspace():
    conn = get_db_connection()
    fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
    unique_models = conn.execute('SELECT DISTINCT model FROM Aircraft').fetchall()
    conn.close()
    return render_template('workspace.html', fleet=fleet, unique_models=unique_models)

@app.route('/add_aircraft', methods=['POST'])
def add_aircraft():
    reg = request.form['registration'].upper()
    model = request.form['model']
    hours = float(request.form['total_flight_hours'])
    cycles = int(request.form['total_cycles'])
    formatted_id = f"Aircraft_{reg.replace('-', '_')}"
    
    pdf_path = ""
    if 'amm_pdf' in request.files:
        file = request.files['amm_pdf']
        if file.filename != '':
            filename = secure_filename(f"AMM_{model}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            pdf_path = filepath
            
    conn = get_db_connection()
    conn.execute('INSERT INTO Aircraft (aircraft_id, registration, model, total_flight_hours, total_cycles, amm_pdf_path) VALUES (?, ?, ?, ?, ?, ?)', 
                 (formatted_id, reg, model, hours, cycles, pdf_path))
    conn.commit()
    conn.close()
    return redirect(url_for('workspace'))

@app.route('/update_amm', methods=['POST'])
def update_amm():
    aircraft_id = request.form['aircraft_id']
    pdf_path = ""
    if 'amm_pdf' in request.files:
        file = request.files['amm_pdf']
        if file.filename != '':
            time_stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = secure_filename(f"AMM_Rev_{time_stamp}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            pdf_path = filepath
            
            conn = get_db_connection()
            conn.execute('UPDATE Aircraft SET amm_pdf_path = ? WHERE aircraft_id = ?', (pdf_path, aircraft_id))
            conn.commit()
            conn.close()
            
    return redirect(url_for('workspace'))

@app.route('/add_directive', methods=['POST'])
def add_directive():
    target_model = request.form['target_model']
    doc_type = request.form['doc_type']
    ref_number = request.form['ref_number']
    description = request.form['description']
    
    pdf_path = ""
    if 'doc_pdf' in request.files:
        file = request.files['doc_pdf']
        if file.filename != '':
            filename = secure_filename(f"{doc_type}_{ref_number}_{file.filename}")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            pdf_path = filepath

    conn = get_db_connection()
    conn.execute('INSERT INTO Directives (target_model, doc_type, ref_number, description, pdf_path) VALUES (?, ?, ?, ?, ?)', 
                 (target_model, doc_type, ref_number, description, pdf_path))
    conn.commit()
    conn.close()
    return redirect(url_for('workspace'))

@app.route('/add_task', methods=['POST'])
def add_task():
    target_model = request.form.get('target_model', 'ALL')
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO MaintenanceTasks (task_id, task_name, task_category, interval_hours, interval_cycles, interval_months, target_model) 
        VALUES (?, ?, "Routine", ?, ?, 0, ?)
    ''', (request.form['task_id'], request.form['task_name'], request.form['interval_hours'], request.form['interval_cycles'], target_model))
    conn.commit()
    conn.close()
    return redirect(url_for('workspace'))

# --- SMART DUE LIST ---
@app.route('/due_list')
def due_list():
    conn = get_db_connection()
    aircraft = conn.execute('SELECT * FROM Aircraft').fetchall()
    tasks = conn.execute('SELECT * FROM MaintenanceTasks').fetchall()
    
    try:
        engineers = conn.execute("SELECT * FROM Engineers WHERE status = 'Active'").fetchall()
    except sqlite3.OperationalError:
        engineers = []

    completed_history = conn.execute('SELECT aircraft_reg, task_description FROM MaintenanceHistory').fetchall()
    completed_set = {f"{r['aircraft_reg']}_{r['task_description'].split(':')[0].replace('Completed ', '')}" for r in completed_history}

    projections = []
    for plane in aircraft:
        for task in tasks:
            task_key = f"{plane['registration']}_{task['task_id']}"
            
            if task_key in completed_set:
                status = "COMPLETED"
                remaining = 99999 
            elif task['interval_hours'] > 0:
                current_hrs, interval = plane['total_flight_hours'], task['interval_hours']
                remaining = round((((current_hrs // interval) + 1) * interval) - current_hrs, 1)
                status = "OVERDUE" if remaining <= 0 else "DUE SOON" if remaining <= 50 else "GOOD"
            elif task['interval_cycles'] > 0:
                current_cyc, interval = plane['total_cycles'], task['interval_cycles']
                remaining = int((((current_cyc // interval) + 1) * interval) - current_cyc)
                status = "OVERDUE" if remaining <= 0 else "DUE SOON" if remaining <= 100 else "GOOD"
                
            projections.append({'registration': plane['registration'], 'task_id': task['task_id'], 'task_name': task['task_name'], 'current_hours': plane['total_flight_hours'], 'current_cycles': plane['total_cycles'], 'interval_hours': task['interval_hours'], 'interval_cycles': task['interval_cycles'], 'remaining': remaining, 'status': status})
    
    conn.close()
    projections.sort(key=lambda x: x['remaining'])
    return render_template('due_list.html', projections=projections, engineers=engineers)

# --- AME CALENDAR & HANGAR SCHEDULE ---
@app.route('/calendar')
def calendar():
    conn = get_db_connection()
    
    try:
        conn.execute("ALTER TABLE Schedule ADD COLUMN status TEXT DEFAULT 'Scheduled'")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
    schedule_data = conn.execute('SELECT rowid as record_id, * FROM Schedule WHERE status = "Scheduled" OR status IS NULL ORDER BY start_time ASC').fetchall()
    
    try:
        engineers = conn.execute("SELECT * FROM Engineers").fetchall()
    except sqlite3.OperationalError:
        engineers = []
        
    conn.close()
    
    events = [{'title': item['title'], 'start': item['start_time'].replace(' ', 'T'), 'end': item['end_time'].replace(' ', 'T'), 'color': item['color']} for item in schedule_data]
    return render_template('calendar.html', events=json.dumps(events), fleet=fleet, schedule_data=schedule_data, engineers=engineers)

@app.route('/sign_off_schedule/<int:record_id>', methods=['POST'])
def sign_off_schedule(record_id):
    emp_id = request.form.get('engineer_id')
    
    conn = get_db_connection()
    engineer = conn.execute('SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?', (emp_id,)).fetchone()
    schedule_item = conn.execute('SELECT rowid as record_id, * FROM Schedule WHERE rowid = ?', (record_id,)).fetchone()
    
    if engineer and schedule_item:
        digital_signature = f"{engineer['full_name']} (Lic: {engineer['license_number']} | Stamp: {engineer['stamp_number']})"
        task_desc = f"Hangar Check: {schedule_item['title']}"
        aircraft_reg = schedule_item['aircraft_id'].replace('Aircraft_', '')
        
        conn.execute('''
            INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
            VALUES (?, ?, ?)
        ''', (aircraft_reg, task_desc, digital_signature))
        
        conn.execute("UPDATE Schedule SET status = 'Completed' WHERE rowid = ?", (record_id,))
        
    conn.commit()
    conn.close()
    return redirect(url_for('calendar'))

@app.route('/schedule_check', methods=['POST'])
def schedule_check():
    aircraft_id = request.form['aircraft_id']
    check_type = request.form['check_type']
    
    start_time = datetime.strptime(request.form['start_time'], '%Y-%m-%dT%H:%M')
    end_time = datetime.strptime(request.form['end_time'], '%Y-%m-%dT%H:%M')
    
    if check_type == 'A-Check': color = "#fd7e14"
    elif check_type == 'B-Check': color = "#6c757d"
    elif check_type == 'C-Check': color = "#212529"
        
    title = f"Scheduled {check_type} ({aircraft_id.replace('Aircraft_', '')})"
    conn = get_db_connection()
    conn.execute('INSERT INTO Schedule (aircraft_id, event_type, title, start_time, end_time, color) VALUES (?, ?, ?, ?, ?, ?)', 
                 (aircraft_id, 'Maintenance', title, start_time.strftime('%Y-%m-%d %H:%M:%S'), end_time.strftime('%Y-%m-%d %H:%M:%S'), color))
    conn.commit()
    conn.close()
    return redirect(url_for('calendar'))

# --- MEL ENGINE ---
@app.route('/mel', methods=['GET', 'POST'])
def mel():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO MEL_Deferrals (aircraft_id, item_description, mel_category, date_deferred) VALUES (?, ?, ?, ?)', 
                     (request.form['aircraft_id'], request.form['item_description'], request.form['mel_category'], datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        return redirect(url_for('mel'))
        
    fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
    raw_deferrals = conn.execute('SELECT * FROM MEL_Deferrals WHERE status = "Active"').fetchall()
    
    try:
        engineers = conn.execute("SELECT * FROM Engineers").fetchall()
    except sqlite3.OperationalError:
        engineers = []

    deferrals = []
    category_limits = {'B': 3, 'C': 10, 'D': 120}
    for d in raw_deferrals:
        days_remaining = category_limits.get(d['mel_category'], 0) - (datetime.now() - datetime.strptime(d['date_deferred'], '%Y-%m-%d %H:%M:%S')).days
        deferrals.append({'deferral_id': d['deferral_id'], 'aircraft_id': d['aircraft_id'], 'item_description': d['item_description'], 'mel_category': d['mel_category'], 'days_remaining': days_remaining})
        
    conn.close()
    deferrals.sort(key=lambda x: x['days_remaining'])
    return render_template('mel.html', fleet=fleet, deferrals=deferrals, engineers=engineers)

@app.route('/resolve_mel/<int:deferral_id>', methods=['POST'])
def resolve_mel(deferral_id):
    emp_id = request.form.get('engineer_id') 
    
    conn = get_db_connection()
    engineer = conn.execute('SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?', (emp_id,)).fetchone()
    deferral = conn.execute('SELECT aircraft_id, item_description FROM MEL_Deferrals WHERE deferral_id = ?', (deferral_id,)).fetchone()
    
    if engineer and deferral:
        digital_signature = f"{engineer['full_name']} (Lic: {engineer['license_number']} | Stamp: {engineer['stamp_number']})"
        task_desc = f"Cleared MEL Deferral: {deferral['item_description']}"
        aircraft_reg = deferral['aircraft_id'].replace('Aircraft_', '')
        
        conn.execute('''
            INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
            VALUES (?, ?, ?)
        ''', (aircraft_reg, task_desc, digital_signature))
        
        conn.execute('UPDATE MEL_Deferrals SET status = "Resolved" WHERE deferral_id = ?', (deferral_id,))
        
    conn.commit()
    conn.close()
    return redirect(url_for('mel'))

# --- DIGITAL TOOL CRIB ---
@app.route('/tool_crib')
def tool_crib():
    conn = get_db_connection()
    raw_tools = conn.execute('SELECT * FROM ToolCrib').fetchall()
    
    try:
        engineers = conn.execute('SELECT * FROM Engineers').fetchall()
    except sqlite3.OperationalError:
        engineers = []
        
    conn.close()
    
    tools = []
    categories = set() 
    for t in raw_tools:
        categories.add(t['category'])
        if t['calibration_due'] == 'N/A' or not t['calibration_due']:
            cal_days = 9999
            cal_due_text = 'N/A'
        else:
            cal_days = (datetime.strptime(t['calibration_due'], '%Y-%m-%d') - datetime.now()).days
            cal_due_text = t['calibration_due']
            
        tools.append({
            'tool_id': t['tool_id'], 'tool_name': t['tool_name'], 'category': t['category'], 
            'calibration_due': cal_due_text, 'status': t['status'], 'checked_out_to': t['checked_out_to'], 
            'cal_days_remaining': cal_days
        })
        
    sorted_categories = sorted(list(categories))
    return render_template('tool_crib.html', tools=tools, categories=sorted_categories, engineers=engineers)

@app.route('/checkout_tool/<tool_id>', methods=['POST'])
def checkout_tool(tool_id):
    emp_id = request.form.get('engineer_id')
    conn = get_db_connection()
    
    engineer = conn.execute('SELECT full_name FROM Engineers WHERE emp_id = ?', (emp_id,)).fetchone()
    eng_name = engineer['full_name'] if engineer else "Unknown Engineer"
    
    conn.execute('UPDATE ToolCrib SET status = "Checked Out", checked_out_to = ? WHERE tool_id = ?', (eng_name, tool_id))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/checkin_tool/<tool_id>', methods=['POST'])
def checkin_tool(tool_id):
    conn = get_db_connection()
    conn.execute('UPDATE ToolCrib SET status = "Available", checked_out_to = "" WHERE tool_id = ?', (tool_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/add_tool', methods=['POST'])
def add_tool():
    conn = get_db_connection()
    conn.execute('INSERT INTO ToolCrib (tool_id, tool_name, category, calibration_due, status) VALUES (?, ?, ?, ?, "Available")',
                 (request.form['tool_id'], request.form['tool_name'], request.form['category'], request.form['calibration_due']))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/remove_tool/<tool_id>', methods=['POST'])
def remove_tool(tool_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM ToolCrib WHERE tool_id = ?', (tool_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/quarantine_tool/<tool_id>', methods=['POST'])
def quarantine_tool(tool_id):
    conn = get_db_connection()
    conn.execute('UPDATE ToolCrib SET status = "Quarantined" WHERE tool_id = ?', (tool_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/update_tool/<tool_id>', methods=['POST'])
def update_tool(tool_id):
    new_date = request.form['new_cal_date']
    conn = get_db_connection()
    conn.execute('UPDATE ToolCrib SET calibration_due = ?, status = "Available" WHERE tool_id = ?', (new_date, tool_id))
    conn.commit()
    conn.close()
    return redirect(url_for('tool_crib'))

@app.route('/remove_aircraft/<aircraft_id>', methods=['POST'])
def remove_aircraft(aircraft_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM Aircraft WHERE aircraft_id = ?', (aircraft_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('workspace'))

# --- THE TRUE AI ONTOLOGY REASONER ENGINE ---
@app.route('/run_reasoner/<aircraft_id>', methods=['GET', 'POST'])
def run_reasoner(aircraft_id):
    conn = get_db_connection()
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS XAILogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            component_id TEXT, 
            ai_decision TEXT, 
            explanation_text TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # FIXED: Fetch strictly the SINGLE most recent reading per sensor per component!
    latest_telemetry = conn.execute('''
        SELECT t1.reading_value, t1.sensor_type, c.component_id 
        FROM SensorTelemetry t1 
        JOIN Components c ON t1.component_id = c.component_id 
        WHERE c.aircraft_id = ? 
          AND t1.recorded_at = (
              SELECT MAX(t2.recorded_at) 
              FROM SensorTelemetry t2 
              WHERE t2.component_id = t1.component_id AND t2.sensor_type = t1.sensor_type
          )
    ''', (aircraft_id,)).fetchall()
    
    if latest_telemetry:
        onto_path.append(".")
        
        # THE MOA UPGRADE: Load the base ontology first, then the 7-layer MOA
        base_onto = get_ontology("camp.owl").load()
        onto = get_ontology("camp_multi_ontology.owl").load()
        
        for idx, t in enumerate(latest_telemetry):
            reading = float(t['reading_value'])
            comp_id = t['component_id']
            sensor_type = t['sensor_type']
            
            # Generate a precise timestamp for the XAI Log
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            ai_fault_detected = None
            severity = "Normal"
            explanation = f"[{timestamp}] Ontology analysis complete: {sensor_type} parameters nominal."
            amm_text = "ATA_05 (General Limits)"
            
            # Generate a globally unique ID for this specific loop
            unique_run_id = uuid.uuid4().hex[:8]
            
            with onto:
                # 1. NAMESPACE FIX: Explicitly call the classes from base_onto!
                test_comp = base_onto.AircraftComponent(f"Comp_{comp_id}_temp_{unique_run_id}")
                test_sensor = base_onto.SensorData(f"Sens_{comp_id}_temp_{unique_run_id}")
                
                # 2. Assign the properties
                test_comp.hasSensorData = [test_sensor] # Object property, keep the list!
                test_sensor.sensorValue = float(reading) # Functional property, NO brackets!
                
                print(f"🧠 Running Pellet Reasoner on {comp_id} (Reading: {reading}, Sensor: {sensor_type})...")
                sync_reasoner_pellet(infer_property_values=True, infer_data_property_values=True)
                
                # Check for inferred faults from the MOA base layer
                inferred_fault_names = [f.name if hasattr(f, 'name') else str(f) for f in test_comp.hasFault]
                
                # 3. CONTEXT-AWARE MOA LOGIC
                if sensor_type == 'Thermocouple' and (reading > 900.0 or "OverTemp" in str(inferred_fault_names)):
                    ai_fault_detected = "Engine_Overheat_Critical"
                    severity = "Critical"
                    amm_text = "ATA_77 (Engine Indicating)"
                    explanation = f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>900°C) from {sensor_type} reading of {reading} on {comp_id}. Manual Assigned: {amm_text}."
                    
                elif sensor_type == 'Vibration Sensor' and (reading > 4.5 or "Vibration" in str(inferred_fault_names)):
                    ai_fault_detected = "Vibration_Imbalance"
                    severity = "High"
                    amm_text = "ATA_72 (Engine)"
                    explanation = f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (>4.5) from {sensor_type} reading of {reading} on {comp_id}. Manual Assigned: {amm_text}."
                    
                elif sensor_type == 'Pressure Sensor' and (reading < 20.0 or "Leak" in str(inferred_fault_names)):
                    ai_fault_detected = "Fuel_Leak_Detected"
                    severity = "Critical"
                    amm_text = "ATA_28 (Fuel Systems)"
                    explanation = f"[{timestamp}] MOA AI TRIGGERED: Reasoner evaluated L3_Behavioral threshold (<20.0) from {sensor_type} reading of {reading} on {comp_id}. Manual Assigned: {amm_text}."

                # Destroy entities immediately to completely prevent resources.iri database collisions
                destroy_entity(test_sensor)
                destroy_entity(test_comp)
                
            ai_action_taken = "Grounded Airframe" if ai_fault_detected else "Cleared for Flight"
            
            conn.execute('INSERT INTO XAILogs (component_id, ai_decision, explanation_text) VALUES (?, ?, ?)', 
                         (comp_id, ai_action_taken, explanation))
            
            if ai_fault_detected:
                existing_fault = conn.execute('SELECT * FROM Faults WHERE component_id = ? AND fault_type = ? AND resolved = 0', (comp_id, ai_fault_detected)).fetchone()
                
                if not existing_fault:
                    conn.execute('INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference) VALUES (?, ?, ?, 0, ?)', (comp_id, ai_fault_detected, severity, amm_text))
                    
        conn.commit()
    else:
        conn.execute('INSERT INTO XAILogs (component_id, ai_decision, explanation_text) VALUES (?, ?, ?)', 
                     ('System', 'Standby', f"No telemetry data found for {aircraft_id} to analyze."))
        conn.commit()

    conn.close()
    return redirect(url_for('dashboard', tail=aircraft_id))

# --- TEMPLATE FILTERS ---
@app.template_filter('abs')
def absolute_value(number):
    return abs(number)

# --- AI REASONER LOGS (XAI HISTORY) ---
@app.route('/xai_reasoner')
def xai_reasoner():
    conn = get_db_connection()
    try:
        logs = conn.execute('SELECT rowid, * FROM XAILogs ORDER BY rowid DESC').fetchall()
    except sqlite3.OperationalError:
        logs = []
    conn.close()
    return render_template('xai_reasoner.html', logs=logs)

# --- PILOT FLIGHT LOG (PIREP) SYNC ROUTE ---
@app.route('/flight_log', methods=['GET', 'POST'])
def flight_log():
    conn = get_db_connection()
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS PilotReports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aircraft_id TEXT,
            reported_by TEXT,
            discrepancy_text TEXT,
            status TEXT DEFAULT 'Open'
        )
    ''')
    
    if request.method == 'POST':
        aircraft_id = request.form.get('aircraft_id')
        reported_by = request.form.get('reported_by').strip()
        discrepancy_text = request.form.get('discrepancy_text').strip() 
        
        cursor = conn.execute('''
            INSERT INTO PilotReports (aircraft_id, reported_by, discrepancy_text) 
            VALUES (?, ?, ?)
        ''', (aircraft_id, reported_by, discrepancy_text))
        pirep_id = cursor.lastrowid 
        
        fault_type = f"PIREP ({reported_by}): {discrepancy_text}"
        unique_airframe_id = f"Airframe_{aircraft_id}"
        
        try:
            conn.execute('INSERT INTO Components (component_id, aircraft_id) VALUES (?, ?)', (unique_airframe_id, aircraft_id))
        except sqlite3.IntegrityError:
            pass 
            
        conn.execute('''
            INSERT INTO Faults (component_id, fault_type, severity, resolved, amm_reference) 
            VALUES (?, ?, 'Pilot Report', 0, ?)
        ''', (unique_airframe_id, fault_type, f"PIREP_ID_{pirep_id}"))
        
        conn.commit()
        return redirect(url_for('flight_log'))

    fleet = conn.execute('SELECT * FROM Aircraft').fetchall()
    reports = conn.execute('SELECT rowid AS record_id, * FROM PilotReports WHERE status = "Open" ORDER BY rowid DESC').fetchall()
        
    conn.close()
    return render_template('flight_log.html', fleet=fleet, reports=reports)


# --- PERSONNEL & ENGINEERS ROSTER ---
@app.route('/personnel')
def personnel():
    conn = get_db_connection()
    try:
        engineers = conn.execute('SELECT * FROM Engineers').fetchall()
    except sqlite3.OperationalError:
        engineers = []
    conn.close()
    return render_template('personnel.html', engineers=engineers)

@app.route('/add_engineer', methods=['POST'])
def add_engineer():
    emp_id = request.form['emp_id']
    full_name = request.form['full_name']
    license_type = request.form['license_type']
    license_number = request.form['license_number']
    stamp_number = request.form['stamp_number']
    
    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO Engineers (emp_id, full_name, license_type, license_number, stamp_number)
            VALUES (?, ?, ?, ?, ?)
        ''', (emp_id, full_name, license_type, license_number, stamp_number))
        conn.commit()
    except sqlite3.IntegrityError:
        pass 
    conn.close()
    return redirect(url_for('personnel'))

# --- DUE LIST DIGITAL SIGN-OFF ---
@app.route('/sign_off_due/<registration>/<task_id>', methods=['POST'])
def sign_off_due(registration, task_id):
    emp_id = request.form.get('engineer_id')
    
    conn = get_db_connection()
    engineer = conn.execute('SELECT full_name, license_number, stamp_number FROM Engineers WHERE emp_id = ?', (emp_id,)).fetchone()
    task = conn.execute('SELECT task_name FROM MaintenanceTasks WHERE task_id = ?', (task_id,)).fetchone()
    
    if engineer and task:
        digital_signature = f"{engineer['full_name']} (Lic: {engineer['license_number']} | Stamp: {engineer['stamp_number']})"
        task_desc = f"Completed {task_id}: {task['task_name']}"
        
        conn.execute('''
            INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
            VALUES (?, ?, ?)
        ''', (registration, task_desc, digital_signature))
        
    conn.commit()
    conn.close()
    return redirect(url_for('due_list'))

# --- MAINTENANCE HISTORY & DIGITAL SIGNATURE LOG ---
@app.route('/history')
def maintenance_history():
    conn = get_db_connection()
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS MaintenanceHistory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aircraft_reg TEXT,
            task_description TEXT,
            signed_off_by TEXT,
            completion_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    routine_history = conn.execute('''
        SELECT aircraft_reg, task_description, signed_off_by, completion_date 
        FROM MaintenanceHistory
        
        UNION
        
        SELECT REPLACE(c.aircraft_id, 'Aircraft_', '') AS aircraft_reg, 
               'Resolved Fault: ' || f.fault_type AS task_description, 
               f.resolved_by AS signed_off_by, 
               f.resolved_date AS completion_date
        FROM Faults f
        JOIN Components c ON f.component_id = c.component_id
        WHERE f.resolved = 1
        
        ORDER BY completion_date DESC
    ''').fetchall()
    
    try:
        crs_records = conn.execute('SELECT * FROM CRS_Records ORDER BY release_date DESC').fetchall()
    except sqlite3.OperationalError:
        crs_records = []
    
    conn.close()
    return render_template('history.html', routine_history=routine_history, crs_records=crs_records)

if __name__ == '__main__':
    print("🚀 C.O.R.E. CAMP (Continuous Ontology Reasoning Engine) is online on PORT 5000!")
    app.run(host='127.0.0.1', port=500, debug=True)