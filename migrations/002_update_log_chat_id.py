from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add the parent directory to the system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, LogSettings

from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Получение строки подключения к базе данных из переменных окружения
DATABASE_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
session = SessionLocal()

# Update the LogSettings model
def upgrade():
    # Alter the log_chat_id column to BigInteger
    with engine.connect() as connection:
        from sqlalchemy import text
        connection.execute(text("ALTER TABLE log_settings ALTER COLUMN log_chat_id TYPE BIGINT USING log_chat_id::bigint;"))

def downgrade():
    # Revert the change if needed
    with engine.connect() as connection:
        connection.execute("ALTER TABLE log_settings ALTER COLUMN log_chat_id TYPE VARCHAR;")