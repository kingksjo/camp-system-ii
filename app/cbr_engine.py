"""
Case-Based Reasoning (CBR) Engine for C.O.R.E. CAMP.
Handles semantic search and similarity matching for historical maintenance cases.
"""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from app.database import get_db
from app.config import Config


def retrieve_similar_cases(current_fault_desc, aircraft_reg, threshold=None):
    """
    Retrieve similar historical maintenance cases for a given fault.
    
    Args:
        current_fault_desc (str): Description of the current fault
        aircraft_reg (str): Aircraft registration number
        threshold (float): Similarity score threshold (0-1). Defaults to config value.
    
    Returns:
        list: Top 3 similar historical cases with similarity scores
    """
    if threshold is None:
        threshold = Config.CBR_SIMILARITY_THRESHOLD
    
    with get_db() as conn:
        # Fetch historical maintenance cases (Retain Phase of CBR)
        historical_cases = conn.execute('''
            SELECT task_description, signed_off_by, completion_date 
            FROM MaintenanceHistory 
            WHERE aircraft_reg = ?
        ''', (aircraft_reg,)).fetchall()
    
    if not historical_cases:
        return []
    
    # Extract text corpus for NLP analysis
    case_texts = [case['task_description'] for case in historical_cases]
    case_texts.insert(0, current_fault_desc)  # Current fault at index 0
    
    # Calculate semantic similarity using TF-IDF & Cosine Similarity
    vectorizer = TfidfVectorizer(stop_words='english', max_features=100)
    tfidf_matrix = vectorizer.fit_transform(case_texts)
    cosine_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
    
    # Retrieve and rank matching cases above threshold
    similar_cases = []
    for idx, score in enumerate(cosine_sim):
        if score >= threshold:
            similar_cases.append({
                'similarity_score': round(score * 100, 1),  # Convert to percentage
                'task_description': historical_cases[idx]['task_description'],
                'mechanic': historical_cases[idx]['signed_off_by'],
                'date': historical_cases[idx]['completion_date']
            })
    
    # Sort by highest similarity and return top results
    similar_cases.sort(key=lambda x: x['similarity_score'], reverse=True)
    return similar_cases[:Config.CBR_TOP_RESULTS]


def log_maintenance_action(aircraft_reg, task_description, digital_signature, conn=None):
    """
    Log a maintenance action to the global history.
    
    Args:
        aircraft_reg (str): Aircraft registration
        task_description (str): Description of maintenance task
        digital_signature (str): Engineer's digital signature
    """
    if conn is not None:
        conn.execute('''
            INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
            VALUES (?, ?, ?)
        ''', (aircraft_reg, task_description, digital_signature))
        return
    
    with get_db() as db_conn:
        db_conn.execute('''
            INSERT INTO MaintenanceHistory (aircraft_reg, task_description, signed_off_by)
            VALUES (?, ?, ?)
        ''', (aircraft_reg, task_description, digital_signature))
        db_conn.commit()
