"""Routes for maintenance document generation + the paper-audit record log (Feature #4)."""
from flask import Blueprint, render_template, send_file, abort
from app.camp_extensions import maintenance_documents as docs

bp = Blueprint('maintenance_documents', __name__)


@bp.route('/maintenance-documents')
def maintenance_documents_page():
    """The 'record log' - a permanent register of every certificate ever issued."""
    docs.ensure_documents_schema()
    records = docs.list_documents()
    return render_template('extensions/maintenance_documents.html', records=records)


@bp.route('/maintenance-documents/generate/<source_type>/<source_id>')
def generate_document(source_type, source_id):
    """Generate (or re-fetch) the PDF for a CRS record or maintenance-log entry and download it."""
    if source_type not in ('crs', 'maintenance_log', 'fault'):
        abort(404)
    try:
        result = docs.generate_document(source_type, source_id)
    except ValueError as e:
        return str(e), 404
    except RuntimeError as e:
        return str(e), 503
    return send_file(result['file_path'], as_attachment=True,
                      download_name=f"CAMP-{source_type.upper()}-{source_id}.pdf")
