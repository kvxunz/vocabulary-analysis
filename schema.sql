-- Main application settings, currently just for the active template
CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active_template_id INTEGER,
    FOREIGN KEY (active_template_id) REFERENCES prompt_templates (id)
);

-- Stores the various prompt templates for generating analyses
CREATE TABLE IF NOT EXISTS prompt_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL
);

-- Caches the raw markdown analysis for each word to avoid repeated API calls
CREATE TABLE IF NOT EXISTS word_cache (
    word TEXT PRIMARY KEY,
    analysis TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Add a trigger to update the timestamp whenever a row is updated
CREATE TRIGGER IF NOT EXISTS update_word_cache_timestamp
AFTER UPDATE ON word_cache
FOR EACH ROW
BEGIN
    UPDATE word_cache SET timestamp = CURRENT_TIMESTAMP WHERE word = NEW.word;
END; 