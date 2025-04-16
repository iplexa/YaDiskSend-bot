from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import sys
import os

# Add the parent directory to the system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, User

from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Получение строки подключения к базе данных из переменных окружения
DATABASE_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
session = SessionLocal()

# Update the User model
def upgrade():
    # Alter the telegram_id column to BigInteger
    with engine.connect() as connection:
        from sqlalchemy import text
        connection.execute(text("ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;"))

def downgrade():
    # Revert the change if needed
    with engine.connect() as connection:
        connection.execute("ALTER TABLE users ALTER COLUMN telegram_id TYPE INTEGER;")

if __name__ == "__main__":
    upgrade()
    print("Migration applied successfully.")
