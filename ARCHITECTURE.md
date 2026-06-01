# C.O.R.E. CAMP - Refactored Architecture

## Overview
The original monolithic `app.py` has been refactored into a modular, maintainable structure following Flask best practices. The application maintains 100% functional equivalence while being much easier to read, test, and extend.

## Directory Structure
```
camp-system-ii/
├── app/                          # Main application package
│   ├── __init__.py              # App factory and initialization
│   ├── config.py                # Configuration management
│   ├── database.py              # Database connection and schema
│   ├── cbr_engine.py            # Case-Based Reasoning engine
│   ├── ontology_reasoner.py     # AI fault detection (MOA/Pellet)
│   ├── utils.py                 # Utility functions
│   └── routes/                  # Route blueprints (feature-based)
│       ├── __init__.py          # Blueprint registration
│       ├── dashboard.py         # Fleet overview & active faults
│       ├── workspace.py         # Aircraft & directive management
│       ├── fault_resolution.py  # Fault sign-off & compliance
│       ├── due_list.py          # Maintenance scheduling
│       ├── calendar.py          # AME calendar & checks
│       ├── mel.py               # Minimum Equipment List
│       ├── tool_crib.py         # Tool tracking & calibration
│       ├── reasoner.py          # Ontology reasoning engine (XAI)
│       ├── flight_log.py        # Pilot reports (PIREPs)
│       ├── personnel.py         # Engineer roster
│       └── history.py           # Maintenance audit trail
├── run.py                        # Application entry point
├── static/                       # Static assets
│   ├── style.css
│   └── uploads/
├── templates/                    # Jinja2 templates (unchanged)
├── archives/                     # Utility scripts
└── requirements.txt              # Python dependencies
```

## Module Descriptions

### Core Application Files

#### `app/__init__.py`
**Purpose:** Application factory and initialization
- `create_app(config_name)` - Factory function to create Flask app
- Loads configuration
- Registers all blueprints
- Sets up template filters
- Creates upload folders

**When to use:** Never run directly; imported by `run.py`

#### `app/config.py`
**Purpose:** Centralized configuration management
- `Config` - Base configuration class
- `DevelopmentConfig` - Development settings
- `ProductionConfig` - Production settings  
- `TestingConfig` - Testing settings

**Key settings:**
- Database path and timeouts
- Ontology file paths
- CBR thresholds
- Upload folder configuration

**When to modify:** 
- Change database location
- Adjust AI parameters
- Configure environment-specific settings

#### `app/database.py`
**Purpose:** Database connection management and schema migrations
- `get_db_connection()` - Get raw SQLite connection
- `get_db()` - Context manager for connections (preferred)
- `_apply_schema_migrations()` - Auto-apply schema changes
- `_create_tables_if_not_exist()` - Ensure tables exist

**Self-Healing Pattern:** Automatically applies ALTER TABLE statements on connection, ensuring schema stays current without manual migrations.

**When to use:** 
- In other modules via `from app.database import get_db`
- Use context manager: `with get_db() as conn: ...`

#### `app/utils.py`
**Purpose:** Shared utility functions
- `create_digital_signature()` - Format engineer signatures
- `save_upload_file()` - Secure file upload handler
- `get_aircraft_registration_from_id()` - Parse aircraft IDs
- `get_aircraft_id_from_registration()` - Format aircraft IDs

**When to use:** Whenever you need these common operations

### AI/Reasoning Engines

#### `app/cbr_engine.py`
**Purpose:** Case-Based Reasoning for semantic maintenance search
- `retrieve_similar_cases()` - Find historical precedents for faults
  - Uses TF-IDF vectorization
  - Returns top 3 matches with similarity scores
  - Configurable threshold (default 0.3 / 30%)

- `log_maintenance_action()` - Record action to global history

**Algorithm:** TF-IDF + Cosine Similarity matching against maintenance history

**When to use:** 
- When displaying faults to mechanics
- Need historical precedents for decision support

#### `app/ontology_reasoner.py`
**Purpose:** MOA (Model-Oriented Architecture) AI reasoning with Pellet
- `OntologyReasoner` class - Main reasoner
  - `analyze_telemetry()` - Analyze single sensor reading
  - `_evaluate_contextual_thresholds()` - Apply MOA L3_Behavioral logic

- `run_fleet_analysis()` - Analyze all aircraft telemetry

**Fault Detection Logic:**
- **Thermocouple** >900°C → Engine_Overheat_Critical (ATA_77)
- **Vibration Sensor** >4.5g → Vibration_Imbalance (ATA_72)
- **Pressure Sensor** <20 PSI → Fuel_Leak_Detected (ATA_28)

**When to use:** 
- Automated fault detection from telemetry
- Generate XAI logs explaining AI decisions

### Route Blueprints

Each blueprint handles one major feature area. All routes are prefixed with their domain.

#### `routes/dashboard.py` - `/`
- Main dashboard with fleet overview
- Active faults with CBR recommendations
- Fleet telemetry and schedule
- Model-aware directive display

#### `routes/workspace.py` - `/workspace`, `/add_aircraft`, etc.
- Aircraft fleet management
- Directive/AD management
- Maintenance task templates
- File uploads (AMM PDFs)

#### `routes/fault_resolution.py` - `/resolve_fault/<id>`
- Ontology compliance checking
- Digital signature generation
- CRS (Certificate of Release to Service) generation
- Sensor data simulation on repair

#### `routes/due_list.py` - `/due_list`
- Maintenance interval tracking
- Predictive scheduling
- Task sign-off workflow
- Uses completed history to avoid duplicates

#### `routes/calendar.py` - `/calendar`, `/schedule_check`
- AME calendar view
- A/B/C check scheduling
- Schedule sign-off with digital signatures

#### `routes/mel.py` - `/mel`
- Minimum Equipment List deferrals
- Category-based limits (B/C/D)
- Days-remaining calculation
- Deferral clearance workflow

#### `routes/tool_crib.py` - `/tool_crib`
- Tool inventory management
- Calibration due date tracking
- Checkout/checkin system
- Tool quarantine on calibration failure

#### `routes/reasoner.py` - `/run_reasoner/<aircraft_id>`, `/xai_reasoner`
- Trigger full fleet telemetry analysis
- Display XAI (eXplainable AI) logs
- Show reasoning decisions and thresholds

#### `routes/flight_log.py` - `/flight_log`
- Pilot report (PIREP) submission
- Auto-fault generation from discrepancies
- Open/closed PIREP tracking

#### `routes/personnel.py` - `/personnel`, `/add_engineer`
- Licensed engineer roster
- License type and stamp tracking
- Digital signature database

#### `routes/history.py` - `/history`
- Complete maintenance audit trail
- Resolved fault history
- CRS (Certificate) records

## Key Design Patterns

### 1. Context Manager for Database Connections
```python
# ✅ Preferred - Auto-closes connection
with get_db() as conn:
    result = conn.execute('SELECT ...')

# ❌ Avoid - Manual close required
conn = get_db_connection()
# ... must remember to close
```

### 2. Blueprint Organization
- Each feature gets its own blueprint
- Imported in `routes/__init__.py`
- Registered in `app/__init__.py`
- Keeps routes organized by domain

### 3. Self-Healing Database
- Schema migrations applied on every connection
- Old code doesn't break with new columns
- No separate migration system needed

### 4. Digital Signatures
```python
digital_signature = create_digital_signature(engineer)
# Output: "John Smith (Lic: AME | Stamp: 12345)"
```

## Running the Application

### Development Mode
```bash
# Method 1: Direct execution
python run.py

# Method 2: Flask CLI
set FLASK_APP=app
flask run
```

### Production Mode
```bash
# Using Gunicorn (recommended)
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Or with environment variable
set FLASK_ENV=production
python run.py
```

### Testing
```bash
set FLASK_ENV=testing
python -m pytest
```

## Adding New Features

### Example: Add a New Route Module

1. **Create** `app/routes/new_feature.py`:
```python
from flask import Blueprint, render_template
from app.database import get_db

bp = Blueprint('new_feature', __name__)

@bp.route('/new_feature')
def new_feature():
    with get_db() as conn:
        data = conn.execute('SELECT ...').fetchall()
    return render_template('new_feature.html', data=data)
```

2. **Register** in `app/routes/__init__.py`:
```python
from app.routes import new_feature

def register_blueprints(app):
    # ... existing blueprints ...
    app.register_blueprint(new_feature.bp)
```

3. **Use** in your code just like existing routes!

## Migration from Old Structure

### File Mapping
| Old Location | New Location |
|--------------|--------------|
| app.py | app/__init__.py + routes/* |
| N/A | app/config.py (new) |
| N/A | app/database.py (extracted) |
| N/A | app/cbr_engine.py (extracted) |
| N/A | app/ontology_reasoner.py (extracted) |
| N/A | app/utils.py (extracted) |
| N/A | run.py (entry point) |

### Import Changes
```python
# Old style (monolithic)
from app import get_db_connection

# New style (modular)
from app.database import get_db  # Context manager
```

## Benefits of Refactoring

✅ **Readability** - Each file ~200 lines vs 800+ line monolith
✅ **Maintainability** - Clear separation of concerns
✅ **Testability** - Easy to mock individual modules
✅ **Reusability** - Import functions without importing everything
✅ **Scalability** - Add features without touching existing code
✅ **Performance** - Lazy loading of modules
✅ **Documentation** - Self-documenting module organization

## Common Tasks

### Add a new database column
1. Update schema migration in `app/database.py`
2. Existing connections will auto-apply

### Add AI fault detection
1. Modify `app/ontology_reasoner.py`
2. Update thresholds in `_evaluate_contextual_thresholds()`

### Change CBR similarity threshold
1. Edit `app/config.py` → `CBR_SIMILARITY_THRESHOLD`
2. Or pass threshold to `retrieve_similar_cases()`

### Add new route
1. Create `app/routes/my_feature.py`
2. Register in `app/routes/__init__.py`

## Troubleshooting

**Q: Import errors after refactoring?**
A: Ensure you're running `python run.py`, not `python app.py`

**Q: Database schema issues?**
A: Delete `camp_system.db` and restart - schema will be recreated

**Q: Blueprints not registering?**
A: Check `app/routes/__init__.py` has all imports and `register_blueprints()` calls

**Q: Templates not found?**
A: Templates folder must remain at root level `templates/`, not inside `app/`
