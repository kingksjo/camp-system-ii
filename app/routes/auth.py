"""
Routes for login/registration/logout and the Company Profile page
(hangar location -> climate/corrosion matching, per-aircraft document
uploads). See app/auth.py for the underlying schema/session logic.
"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from app.database import get_db
from app.utils import save_upload_file
from app.auth import (
    ensure_auth_schema, match_climate_profile, get_current_company_id,
    get_current_company, REFERENCE_CLIMATE_PROFILES, DEFAULT_COMPANY_ID,
    MANUAL_TYPES, TIER_LABELS
)
from app.camp_extensions.environmental_stressor import sync_company_environment

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard.dashboard'))
    return render_template('auth/login.html', next_url=request.args.get('next', ''))


@bp.route('/login', methods=['POST'])
def do_login():
    ensure_auth_schema()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')

    with get_db() as conn:
        user = conn.execute('SELECT * FROM Users WHERE username = ?', (username,)).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        return render_template('auth/login.html', error='Invalid username or password.',
                                next_url=request.form.get('next', ''))

    session['user_id'] = user['user_id']
    session['username'] = user['username']
    session['company_id'] = user['company_id']

    next_url = request.form.get('next') or url_for('dashboard.dashboard')
    return redirect(next_url)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/register', methods=['GET'])
def register():
    if session.get('user_id'):
        return redirect(url_for('dashboard.dashboard'))
    return render_template('auth/register.html')


@bp.route('/register', methods=['POST'])
def do_register():
    """New company + first admin user - the onboarding entry point.
    Round-3 scope: single-tenant login today, but this already writes a
    real Companies row and stamps company_id on the new user, so a future
    multi-tenant rollout doesn't need to touch this flow at all."""
    ensure_auth_schema()

    company_name = request.form.get('company_name', '').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    full_name = request.form.get('full_name', '').strip()

    if not company_name or not username or not password:
        return render_template('auth/register.html', error='Company name, username, and password are required.')

    with get_db() as conn:
        existing = conn.execute('SELECT 1 FROM Users WHERE username = ?', (username,)).fetchone()
        if existing:
            return render_template('auth/register.html', error=f'Username "{username}" is already taken.')

        # The SELECT above is a courtesy check, not the real guard - two
        # rapid/duplicate submits (double-click, or a resubmit after a slow
        # response) can both pass it before either commits. The UNIQUE
        # constraint on Users.username is the actual source of truth, so it
        # has to be caught here too, or the second request crashes with an
        # unhandled IntegrityError instead of showing the same friendly
        # message the first check would have given.
        try:
            cur = conn.execute(
                'INSERT INTO Companies (company_name) VALUES (?)', (company_name,)
            )
            new_company_id = cur.lastrowid

            cur2 = conn.execute(
                'INSERT INTO Users (company_id, username, password_hash, full_name, role) VALUES (?, ?, ?, ?, ?)',
                (new_company_id, username, generate_password_hash(password), full_name or username, 'Admin')
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return render_template('auth/register.html', error=f'Username "{username}" is already taken.')

        session['user_id'] = cur2.lastrowid
        session['username'] = username
        session['company_id'] = new_company_id

    # Straight into hangar-location setup - "at the beginning of their use"
    return redirect(url_for('auth.company_profile', onboarding=1))


@bp.route('/company-profile', methods=['GET'])
def company_profile():
    ensure_auth_schema()
    company_id = get_current_company_id()
    with get_db() as conn:
        company = conn.execute('SELECT * FROM Companies WHERE company_id = ?', (company_id,)).fetchone()
        fleet = conn.execute(
            'SELECT * FROM Aircraft WHERE company_id = ? OR company_id IS NULL ORDER BY registration', (company_id,)
        ).fetchall()
        models = sorted({plane['model'] for plane in fleet if plane['model']})

        # Left-join IngestedDocuments so every AircraftDocuments row carries
        # its tier/manual_type classification (Registration/Insurance/Other
        # rows predate this table and simply won't have a match, which is
        # correct - they're not part of the ingestion pipeline).
        docs_by_aircraft = {}
        for row in conn.execute('''
            SELECT ad.*, idoc.ingestion_id AS ingestion_id, idoc.manual_type AS ingested_manual_type,
                   idoc.tier AS ingested_tier, idoc.scope AS ingested_scope, idoc.status AS ingestion_status
            FROM AircraftDocuments ad
            LEFT JOIN IngestedDocuments idoc ON idoc.doc_id = ad.doc_id
            WHERE ad.company_id = ?
            ORDER BY ad.uploaded_at DESC
        ''', (company_id,)).fetchall():
            docs_by_aircraft.setdefault(row['aircraft_id'], []).append(row)

    return render_template(
        'auth/company_profile.html',
        company=company,
        fleet=fleet,
        models=models,
        docs_by_aircraft=docs_by_aircraft,
        manual_types=MANUAL_TYPES,
        tier_labels=TIER_LABELS,
        onboarding=request.args.get('onboarding'),
        matched=request.args.get('matched'),
        distance=request.args.get('distance'),
        reference_profiles=REFERENCE_CLIMATE_PROFILES
    )


@bp.route('/company-profile/hangar-location', methods=['POST'])
def update_hangar_location():
    company_id = get_current_company_id()
    location_name = request.form.get('location_name', '').strip()
    try:
        lat = float(request.form['latitude'])
        lon = float(request.form['longitude'])
    except (KeyError, ValueError):
        return redirect(url_for('auth.company_profile'))

    profile_key, profile, distance_km = match_climate_profile(lat, lon)

    with get_db() as conn:
        conn.execute(
            'UPDATE Companies SET hangar_location_name = ?, hangar_latitude = ?, hangar_longitude = ?, '
            'climate_profile_key = ?, corrosion_category = ?, ambient_temp_c = ?, humidity_pct = ?, '
            'updated_at = CURRENT_TIMESTAMP WHERE company_id = ?',
            (location_name or profile['label'], lat, lon, profile_key, profile['corrosion_category'],
             profile['ambient_temp_c'], profile['humidity_pct'], company_id)
        )
        conn.commit()

    sync_company_environment(company_id)
    return redirect(url_for('auth.company_profile', matched=profile_key, distance=distance_km))


@bp.route('/company-profile/rename', methods=['POST'])
def rename_company():
    company_id = get_current_company_id()
    new_name = request.form.get('company_name', '').strip()
    if new_name:
        with get_db() as conn:
            conn.execute('UPDATE Companies SET company_name = ?, updated_at = CURRENT_TIMESTAMP WHERE company_id = ?',
                         (new_name, company_id))
            conn.commit()
    return redirect(url_for('auth.company_profile'))


@bp.route('/company-profile/upload-document', methods=['POST'])
def upload_aircraft_document():
    """Attach a technical manual/document to the fleet - either a specific
    tail number or every aircraft of a given model (most Tier 1/2/3 manuals
    - MMEL, IPC, WDM, CMM, ADs - are issued per aircraft *type*, not per
    physical airframe, so fleet-wide is the more realistic default for
    those). Writes the physical file once, then records it in
    AircraftDocuments (existing, per-tail file registry) and
    IngestedDocuments (new, tier/manual-type classification for the
    ingestion pipeline - see ROUND5_PLAN_document_ingestion.md) - one row of
    each per aircraft it applies to.
    """
    ensure_auth_schema()
    company_id = get_current_company_id()
    doc_label = request.form.get('doc_label', '').strip()
    manual_type = request.form.get('manual_type', 'Other')
    scope = request.form.get('scope', 'single')
    model = request.form.get('model', '').strip()
    aircraft_id = request.form.get('aircraft_id', '')

    if 'document' not in request.files or request.files['document'].filename == '':
        return redirect(url_for('auth.company_profile'))

    manual_info = MANUAL_TYPES.get(manual_type, MANUAL_TYPES['Other'])
    tier = manual_info['tier']

    with get_db() as conn:
        if scope == 'fleet' and model:
            target_aircraft = conn.execute(
                'SELECT aircraft_id FROM Aircraft WHERE model = ? AND (company_id = ? OR company_id IS NULL)',
                (model, company_id)
            ).fetchall()
            target_ids = [row['aircraft_id'] for row in target_aircraft]
        elif aircraft_id:
            # Validate ownership rather than trusting the submitted id
            # outright - a tampered form value shouldn't be able to attach
            # a document to another company's aircraft.
            owned = conn.execute(
                'SELECT aircraft_id FROM Aircraft WHERE aircraft_id = ? AND (company_id = ? OR company_id IS NULL)',
                (aircraft_id, company_id)
            ).fetchone()
            target_ids = [owned['aircraft_id']] if owned else []
        else:
            return redirect(url_for('auth.company_profile'))

        if not target_ids:
            return redirect(url_for('auth.company_profile'))

        # Save the physical file once, reuse the same path for every
        # aircraft this document applies to.
        file_path = save_upload_file(request.files['document'], f"DOC_{manual_type}")
        if not file_path:
            return redirect(url_for('auth.company_profile'))

        label = doc_label or f"{manual_info['label']} ({request.files['document'].filename})"

        for target_id in target_ids:
            cur = conn.execute(
                'INSERT INTO AircraftDocuments (aircraft_id, company_id, doc_label, doc_type, file_path) '
                'VALUES (?, ?, ?, ?, ?)',
                (target_id, company_id, label, manual_type, file_path)
            )
            conn.execute(
                'INSERT INTO IngestedDocuments '
                '(doc_id, company_id, manual_type, tier, scope, target_model, classification_method) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (cur.lastrowid, company_id, manual_type, tier, scope,
                 model if scope == 'fleet' else None, 'user_selected')
            )
        conn.commit()

    return redirect(url_for('auth.company_profile'))

    file_path = save_upload_file(request.files['document'], f"DOC_{aircraft_id}")
    if file_path:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO AircraftDocuments (aircraft_id, company_id, doc_label, doc_type, file_path) '
                'VALUES (?, ?, ?, ?, ?)',
                (aircraft_id, company_id, doc_label or request.files['document'].filename, doc_type, file_path)
            )
            conn.commit()

    return redirect(url_for('auth.company_profile'))
