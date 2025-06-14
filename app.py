import re
import sqlite3
from flask import Flask, jsonify, render_template, request, g, send_from_directory
import markdown
import os
import requests
import time
from dotenv import load_dotenv
import logging
from openai import OpenAI

# --- App Configuration ---
load_dotenv()
app = Flask(__name__)
app.config['DATABASE'] = 'word_cache.db'
app.config['SECRET_KEY'] = os.urandom(24)

# --- Logging ---
logging.basicConfig(level=logging.INFO)

# --- API Configurations ---
# We will now use the OPENAI_API_KEY for both services.
# The GEMINI_API_KEY is no longer necessary if you use OpenAI for analysis.
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if OPENAI_API_KEY:
    logging.info("Successfully loaded OPENAI_API_KEY.")
else:
    logging.error("Failed to load OPENAI_API_KEY. Check .env file.")

# The following are no longer used if OpenAI is used for both services.
# GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
# TTS_API_KEY = os.getenv('TTS_API_KEY')
# GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent'
# TTS_API_URL = 'http://api.voicerss.org/'

AUDIO_CACHE_DIR = 'static/audio_cache'
MAX_RETRIES = 3
TIMEOUT = 60

# --- Database Functions ---
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_connection(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()
    os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)

def parse_vocabulary_file(file_path):
    # This function correctly parses the v.py style type.txt
    units = []
    current_unit = None
    current_group = None
    previous_line = None
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith('==='):
            if current_unit: units.append(current_unit)
            current_unit = {'title': '', 'groups': []}
            previous_line = None
        elif line.startswith('+++'):
            if current_unit and previous_line:
                current_unit['title'] = previous_line
                previous_line = None
        elif line.startswith('---'):
            if current_group is not None and current_unit:
                current_unit['groups'].append(current_group)
            current_group = []
        else:
            previous_line = line
            if current_group is not None and current_unit and current_unit['title']:
                current_group.append(line)
    if current_group is not None and current_unit: current_unit['groups'].append(current_group)
    if current_unit: units.append(current_unit)
    return units

# --- Word Analysis ---
def get_word_analysis(word, template_content, force_reanalyze=False):
    db = get_db()
    if not force_reanalyze:
        cached = db.execute('SELECT analysis FROM word_cache WHERE word = ?', (word,)).fetchone()
        if cached:
            return cached['analysis'], None

    if not OPENAI_API_KEY:
        return None, "OPENAI_API_KEY is not set."

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"请分析单词 \"{word}\"，按照以下结构输出（保持markdown格式）：\n\n{template_content}"
    
    for _ in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo", # Or "gpt-4" if you prefer higher quality
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that analyzes vocabulary words in Markdown format."},
                    {"role": "user", "content": prompt}
                ],
                timeout=TIMEOUT
            )
            analysis_text = response.choices[0].message.content
            # Simple clean up
            analysis_text = re.sub(r'^好的，这是对.*?的词源分析：\s*', '', analysis_text, flags=re.IGNORECASE)
            analysis_text = re.sub(r'^#\s*.*\s*', '', analysis_text).strip()
            return analysis_text, None
        except Exception as e:
            logging.error(f"OpenAI analysis request failed: {e}")
            time.sleep(1)
    return None, f"API request failed after {MAX_RETRIES} retries."

# --- Main Routes ---
@app.route('/')
def index():
    vocabulary_data = parse_vocabulary_file('type.txt')
    return render_template('index.html', units=vocabulary_data)

@app.route('/analyze', methods=['POST'])
def analyze():
    word = request.form['word']
    db = get_db()
    
    cached = db.execute('SELECT analysis FROM word_cache WHERE word = ?', (word,)).fetchone()
    if cached:
        analysis_html = markdown.markdown(cached['analysis'], extensions=['fenced_code', 'tables'])
        return jsonify({'analysis': analysis_html})

    active_template = db.execute('SELECT content FROM prompt_templates WHERE id = (SELECT active_template_id FROM app_settings WHERE id = 1)').fetchone()
    if not active_template:
        return jsonify({'error': 'No active template found.'}), 400

    analysis_text, error = get_word_analysis(word, active_template['content'])
    if error:
        return jsonify({'error': error}), 500
    
    db.execute('INSERT OR REPLACE INTO word_cache (word, analysis) VALUES (?, ?)', (word, analysis_text))
    db.commit()
    
    analysis_html = markdown.markdown(analysis_text, extensions=['fenced_code', 'tables'])
    return jsonify({'analysis': analysis_html})

@app.route('/reanalyze', methods=['POST'])
def reanalyze():
    word = request.form['word']
    db = get_db()
    active_template = db.execute('SELECT content FROM prompt_templates WHERE id = (SELECT active_template_id FROM app_settings WHERE id = 1)').fetchone()
    if not active_template:
        return jsonify({'error': 'No active template found.'}), 400

    analysis_text, error = get_word_analysis(word, active_template['content'], force_reanalyze=True)
    if error:
        return jsonify({'error': error}), 500
    
    db.execute('INSERT OR REPLACE INTO word_cache (word, analysis) VALUES (?, ?)', (word, analysis_text))
    db.commit()
    
    # For re-analysis, frontend expects raw markdown to be parsed by `marked.js`
    return jsonify({'analysis': analysis_text})

@app.route('/tts', methods=['GET'])
def tts():
    word = request.args.get('word')
    if not word: 
        return jsonify({"error": "No word provided"}), 400
    
    if not OPENAI_API_KEY:
        return jsonify({"error": "OpenAI TTS service not configured"}), 503

    filename = "".join(c for c in word if c.isalnum()) + '.mp3'
    filepath = os.path.join(AUDIO_CACHE_DIR, filename)

    if os.path.exists(filepath):
        return send_from_directory(AUDIO_CACHE_DIR, filename)

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=word
        )
        
        # Stream the audio to the file
        response.stream_to_file(filepath)
        
        return send_from_directory(AUDIO_CACHE_DIR, filename)
    except Exception as e:
        # Log the actual error for debugging
        logging.error(f"OpenAI TTS generation failed for word '{word}': {e}")
        return jsonify({"error": f"TTS generation failed"}), 500

# --- API Routes for Template Management ---
@app.route('/api/templates', methods=['GET'])
def get_templates():
    templates = get_db().execute('SELECT id, name FROM prompt_templates ORDER BY name').fetchall()
    return jsonify([dict(t) for t in templates])

@app.route('/api/templates/<int:id>', methods=['GET'])
def get_template(id):
    template = get_db().execute('SELECT * FROM prompt_templates WHERE id = ?', (id,)).fetchone()
    return jsonify(dict(template)) if template else ('', 404)

@app.route('/api/templates', methods=['POST'])
def create_template():
    data = request.get_json()
    if not data or 'name' not in data or 'content' not in data:
        return jsonify({'error': 'Name and content are required'}), 400
    try:
        cursor = get_db().cursor()
        cursor.execute('INSERT INTO prompt_templates (name, content) VALUES (?, ?)', (data['name'], data['content']))
        new_id = cursor.lastrowid
        get_db().commit()
        return jsonify({'message': 'Template created', 'id': new_id}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'A template with this name already exists'}), 409

@app.route('/api/templates/<int:id>', methods=['PUT'])
def update_template(id):
    data = request.get_json()
    if not data or 'name' not in data or 'content' not in data:
        return jsonify({'error': 'Name and content are required'}), 400
    try:
        get_db().execute('UPDATE prompt_templates SET name = ?, content = ? WHERE id = ?', (data['name'], data['content'], id))
        get_db().commit()
        return jsonify({'message': 'Template updated'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'A template with this name already exists'}), 409

@app.route('/api/templates/<int:id>', methods=['DELETE'])
def delete_template(id):
    db = get_db()
    if db.execute('SELECT COUNT(*) FROM prompt_templates').fetchone()[0] <= 1:
        return jsonify({'error': 'Cannot delete the last template'}), 400
    
    active_id = db.execute('SELECT active_template_id FROM app_settings WHERE id = 1').fetchone()['active_template_id']
    if active_id == id:
        new_active = db.execute('SELECT id FROM prompt_templates WHERE id != ? ORDER BY id LIMIT 1', (id,)).fetchone()
        db.execute('UPDATE app_settings SET active_template_id = ? WHERE id = 1', (new_active['id'],))
        
    db.execute('DELETE FROM prompt_templates WHERE id = ?', (id,))
    db.commit()
    return jsonify({'message': 'Template deleted'})

@app.route('/api/templates/active', methods=['GET'])
def get_active_template():
    template = get_db().execute('SELECT t.* FROM prompt_templates t JOIN app_settings s ON t.id = s.active_template_id WHERE s.id = 1').fetchone()
    return jsonify(dict(template)) if template else ('', 404)

@app.route('/api/templates/active', methods=['POST'])
def set_active_template():
    data = request.get_json()
    if not data or 'id' not in data:
        return jsonify({'error': 'Template ID is required'}), 400
    get_db().execute('UPDATE app_settings SET active_template_id = ? WHERE id = 1', (data['id'],))
    get_db().commit()
    return jsonify({'message': 'Active template updated'})

# --- Initialization ---
def initialize_app():
    init_db()
    with app.app_context():
        db = get_db()
        # Ensure there's at least one template
        if db.execute('SELECT COUNT(*) FROM prompt_templates').fetchone()[0] == 0:
            try:
                with open('word_template.md', 'r', encoding='utf-8') as f:
                    default_content = f.read()
                db.execute('INSERT INTO prompt_templates (name, content) VALUES (?, ?)', ('Default', default_content))
                db.commit()
            except FileNotFoundError:
                db.execute('INSERT INTO prompt_templates (name, content) VALUES (?, ?)', ('Default', 'Please provide a default template.'))
                db.commit()

        # Ensure there's an active template setting
        if db.execute('SELECT COUNT(*) FROM app_settings').fetchone()[0] == 0:
            first_template_id = db.execute('SELECT id FROM prompt_templates LIMIT 1').fetchone()[0]
            db.execute('INSERT INTO app_settings (id, active_template_id) VALUES (1, ?)', (first_template_id,))
            db.commit()

initialize_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8001, debug=True)
