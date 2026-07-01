# C.O.R.E. CAMP (Continuous Ontology Reasoning Engine)

An intelligent Aircraft Maintenance Management (AMM) system that integrates Semantic Web technologies and Machine Learning to automate fault detection and provide intelligent maintenance recommendations.

## Overview

C.O.R.E. CAMP leverages ontology-driven reasoning and case-based reasoning (CBR) to enhance aircraft maintenance operations. The system automatically detects faults from sensor data, retrieves historical maintenance solutions, and manages fleet maintenance records with digital sign-offs.

### Core Features
- **Ontology-Based Reasoning:** Real-time fault detection using SWRL rules and the Pellet reasoner
- **Case-Based Reasoning (CBR):** Semantic search for historical maintenance cases using NLP and similarity matching
- **Fleet Management:** Track aircraft registration, maintenance history, personnel licenses, and digital sign-offs
- **Telemetry Analysis:** Evaluate sensor data against ontology rules (e.g., overheating, vibration detection)
- **Web Interface:** User-friendly dashboard for maintenance tracking and recommendations

## Technology Stack

- **Backend:** Python 3.8+ with Flask
- **Ontology Engine:** Owlready2 with Pellet reasoner
- **Database:** SQLite with self-healing schema migrations
- **AI/ML:**
  - Case-Based Reasoning: Scikit-learn (TF-IDF + Cosine Similarity)
  - Ontology Reasoning: SWRL rules and Pellet inference
- **Frontend:** Jinja2 templates with Vanilla CSS

## Prerequisites

- Python 3.8 or higher
- Java (required for Pellet reasoner used by Owlready2)
- pip (Python package manager)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd camp-system-ii
```

2. Create a virtual environment:
```bash
python -m venv .venv
```

3. Activate the virtual environment:
   - **Windows (PowerShell):**
   ```bash
   .venv\Scripts\Activate.ps1
   ```
   - **macOS/Linux:**
   ```bash
   source .venv/bin/activate
   ```

4. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

Start the Flask development server:
```bash
python run.py
```

The application will be available at `http://127.0.0.1:5000`

## Project Structure

```
camp-system-ii/
├── run.py                      # Main Flask application entry point
├── app/                        # Application package (factory, routes, database, etc.)
├── camp.owl                    # Primary OWL ontology
├── camp_multi_ontology.owl     # Extended ontology for multi-aircraft scenarios
├── requirements.txt            # Python dependencies
├── static/                     # Static assets (CSS, uploads)
│   ├── style.css
│   └── uploads/
├── templates/                  # Jinja2 HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── flight_log.html
│   ├── history.html
│   ├── personnel.html
│   └── ...
└── archives/                   # Utility and maintenance scripts
    ├── rebuild_rules.py        # Re-inject SWRL rules into ontology
    ├── setup_personnel.py      # Initialize personnel database
    ├── update_db.py            # Manual database schema updates
    └── ...
```

## Development Conventions

### Ontology-Driven Logic
- Maintenance rules are defined as SWRL (Semantic Web Rule Language) in `archives/rebuild_rules.py`
- After modifying rule definitions, run `rebuild_rules.py` to persist changes to the `.owl` files:
  ```bash
  python archives/rebuild_rules.py
  ```

### Database Management
- The application uses a "Self-Healing" database pattern in `app/database.py`
- Schema additions (e.g., `ALTER TABLE`) are automatically performed on connection
- Manual migrations can be run using `archives/update_db.py`

### Semantic Search (CBR)
- The `retrieve_similar_cases` function compares current fault descriptions against historical maintenance records
- Uses NLP techniques (TF-IDF + Cosine Similarity) for semantic matching

### Digital Signatures
- All maintenance tasks require an engineer's ID for sign-off
- System captures engineer name, license number, and timestamp for audit trails

## Maintenance Scripts

The `archives/` directory contains utility scripts for system initialization and maintenance:

| Script | Purpose |
|--------|---------|
| `rebuild_rules.py` | Wipe and re-inject the 10 official CAMP ontology rules |
| `setup_personnel.py` | Initialize engineers/personnel database |
| `update_db.py` | Perform manual database schema updates |
| `clean_sync.py` | Maintenance script for synchronizing state |

## Deployment

For production deployment, consider:
- Using a production WSGI server (e.g., Gunicorn, uWSGI)
- Configuring environment variables for database and API settings
- Setting up proper logging and monitoring
- Securing the Flask application with HTTPS

## Troubleshooting

**Pellet Reasoner Issues:**
- Ensure Java is installed and accessible in your PATH
- Check that `JAVA_HOME` environment variable is set correctly

**Database Errors:**
- The application automatically handles schema migrations
- If issues persist, review the logs in `app/database.py` for the self-healing mechanism

**Import Errors:**
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Ensure you're using the correct Python virtual environment

## Contributing

1. Follow the ontology-driven development conventions
2. Update rules in `archives/rebuild_rules.py` and run it after changes
3. Test database migrations with `archives/update_db.py`
4. Ensure all maintenance history is properly logged

## License

[Add your license information here]

## Support

For issues and questions, please refer to the project documentation or contact the development team.
