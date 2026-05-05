# C.O.R.E. CAMP (Continuous Ontology Reasoning Engine)

## Project Overview
C.O.R.E. CAMP is a sophisticated Aircraft Maintenance Management (AMM) system that integrates Semantic Web technologies and Machine Learning to automate fault detection and provide intelligent maintenance recommendations.

### Core Technologies
- **Backend:** Python (Flask)
- **Ontology Engine:** [Owlready2](https://owlready2.readthedocs.io/) with the Pellet reasoner.
- **Database:** SQLite (with self-healing schema migrations in `app.py`).
- **AI/ML:**
  - **Case-Based Reasoning (CBR):** Semantic search for historical maintenance cases using `scikit-learn` (TF-IDF + Cosine Similarity).
  - **Ontology-Based Reasoning:** Real-time fault detection using SWRL rules and the Pellet reasoner.
- **Frontend:** Jinja2 templates with Vanilla CSS.

### Architecture
The system follows a Model-Aware architecture where maintenance logic is driven by an OWL ontology (`camp.owl` and `camp_multi_ontology.owl`).
1.  **Telemetry Analysis:** Sensor data is evaluated against ontology rules to detect faults (e.g., overheating, vibration).
2.  **CBR Engine:** When a fault is detected, the system retrieves similar historical fixes to assist mechanics.
3.  **Fleet Management:** Tracks aircraft registration, maintenance history, personnel (licensed engineers), and digital sign-offs.

## Building and Running

### Prerequisites
- Python 3.8+
- Java (Required for the Pellet reasoner used by Owlready2)

### Installation
```bash
pip install flask owlready2 scikit-learn
```

### Running the Application
To start the Flask server:
```bash
python app.py
```
*Note: The application is configured to run on `http://127.0.0.1:500` by default (though logs may mention port 5000).*

### Maintenance & Setup Scripts
The `archives/` directory contains several utility scripts for system initialization and maintenance:
- **`archives/rebuild_rules.py`**: Wipes and re-injects the 10 official CAMP ontology rules into `camp.owl`.
- **`archives/setup_personnel.py`**: Initializes the engineers/personnel database.
- **`archives/update_db.py`**: Utility for manual database schema updates.
- **`archives/clean_sync.py`**: Maintenance script for synchronizing state.

## Development Conventions

### Ontology-Driven Logic
- Rules are defined as SWRL (Semantic Web Rule Language) in `archives/rebuild_rules.py`. 
- Always run `rebuild_rules.py` after modifying rule definitions to persist changes to the `.owl` files.

### Database Management
- The application uses a "Self-Healing" database pattern in `app.py`'s `get_db_connection()`. Schema additions (e.g., `ALTER TABLE`) are performed on connection to ensure the database remains up-to-date with new features.

### Semantic Search (CBR)
- The `retrieve_similar_cases` function in `app.py` handles semantic matching. It compares current fault descriptions against the `MaintenanceHistory` table using NLP techniques.

### Digital Signatures
- Maintenance tasks require an engineer's ID for sign-off. The system captures the engineer's name, license number, and stamp to create a digital signature in the logs.
