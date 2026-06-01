"""
Utility functions for C.O.R.E. CAMP.
"""
from werkzeug.utils import secure_filename
import os
from app.config import Config


def create_digital_signature(engineer):
    """
    Create a digital signature from engineer data.
    
    Args:
        engineer (dict): Engineer record with full_name, license_type, stamp_number
    
    Returns:
        str: Formatted digital signature
    """
    keys = engineer.keys() if hasattr(engineer, 'keys') else engineer
    license_type = engineer['license_type'] if 'license_type' in keys else engineer['license_number'] if 'license_number' in keys else 'N/A'
    stamp = engineer['stamp_number'] if 'stamp_number' in keys else 'N/A'
    return f"{engineer['full_name']} (Lic: {license_type} | Stamp: {stamp})"


def save_upload_file(file, prefix=''):
    """
    Save uploaded file securely.
    
    Args:
        file: File object from Flask request
        prefix (str): Prefix for filename
    
    Returns:
        str: Saved filepath or empty string if no file
    """
    if not file or file.filename == '':
        return ""
    
    filename = secure_filename(f"{prefix}_{file.filename}" if prefix else file.filename)
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    filepath = os.path.join(Config.UPLOAD_FOLDER, filename)
    file.save(filepath)
    
    return filepath


def get_aircraft_registration_from_id(aircraft_id):
    """
    Extract aircraft registration from formatted ID.
    
    Args:
        aircraft_id (str): ID like 'Aircraft_5N_TAJ'
    
    Returns:
        str: Registration like '5N-TAJ'
    """
    if not aircraft_id:
        return "UNKNOWN"
    return aircraft_id.replace('Aircraft_', '').replace('_', '-')


def get_aircraft_id_from_registration(registration):
    """
    Format aircraft registration into ID.
    
    Args:
        registration (str): Registration like '5N-TAJ'
    
    Returns:
        str: Formatted ID like 'Aircraft_5N_TAJ'
    """
    return f"Aircraft_{registration.upper().replace('-', '_')}"
