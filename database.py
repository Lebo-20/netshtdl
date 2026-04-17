import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

async def init_db():
    """Initializes the database and creates the table if it doesn't exist."""
    if not DATABASE_URL:
        logger.error("DATABASE_URL not found in environment variables!")
        return False
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_dramas (
                id SERIAL PRIMARY KEY,
                drama_id TEXT UNIQUE NOT NULL,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.close()
        logger.info("Database initialized successfully.")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        return False

async def is_processed(drama_id):
    """Checks if a drama_id has already been processed."""
    if not DATABASE_URL:
        return False
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        row = await conn.fetchrow('SELECT 1 FROM processed_dramas WHERE drama_id = $1', str(drama_id))
        await conn.close()
        return row is not None
    except Exception as e:
        logger.error(f"Error checking drama_id {drama_id}: {e}")
        return False

async def save_processed_db(drama_id, title):
    """Saves a drama_id and title to the database."""
    if not DATABASE_URL:
        return False
        
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute('''
            INSERT INTO processed_dramas (drama_id, title)
            VALUES ($1, $2)
            ON CONFLICT (drama_id) DO NOTHING
        ''', str(drama_id), title)
        await conn.close()
        logger.info(f"Saved to database: {title} ({drama_id})")
        return True
    except Exception as e:
        logger.error(f"Error saving to database: {e}")
        return False
