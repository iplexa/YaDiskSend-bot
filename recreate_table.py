import os
import sys

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import engine, Base

# Drop and recreate users table
print("Удаляем существующую таблицу users...")
Base.metadata.tables['users'].drop(bind=engine)

print("Создаем таблицу users заново...")
Base.metadata.tables['users'].create(bind=engine)

print("Таблица users успешно пересоздана с типом telegram_id: BigInteger")