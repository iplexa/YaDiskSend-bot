import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, func, Text, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Получение строки подключения к базе данных из переменных окружения
DATABASE_URL = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"

# Создание движка SQLAlchemy
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600, connect_args={'connect_timeout': 10})

# Создание базового класса для моделей
Base = declarative_base()

# Определение модели пользователя
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    
    def __repr__(self):
        return f"<User(id={self.id}, telegram_id={self.telegram_id}, full_name={self.full_name}, is_admin={self.is_admin})>"

# Модель для хранения настроек шаблона имени файла
class FileTemplate(Base):
    __tablename__ = "file_templates"
    
    id = Column(Integer, primary_key=True)
    template = Column(String, nullable=False, default="[фамилия]_ПКС12_[тип]")
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<FileTemplate(id={self.id}, template={self.template})>"

# Модель для хранения настроек логирования
class LogSettings(Base):
    __tablename__ = "log_settings"
    
    id = Column(Integer, primary_key=True)
    log_chat_id = Column(BigInteger, nullable=True)
    log_registrations = Column(Boolean, default=True)
    log_file_uploads = Column(Boolean, default=True)
    
    def __repr__(self):
        return f"<LogSettings(id={self.id}, log_chat_id={self.log_chat_id})>"

# Модель для хранения информации о загруженных файлах
class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    file_name = Column(String, nullable=False)
    file_type = Column(String, nullable=False)  # 'essay' или 'presentation'
    file_content = Column(Text, nullable=False)  # содержимое файла для быстрого сравнения
    file_path = Column(String, nullable=False)  # путь на Яндекс.Диске
    created_at = Column(DateTime, default=func.now())
    
    def __repr__(self):
        return f"<UploadedFile(id={self.id}, user_id={self.user_id}, file_name={self.file_name}, file_type={self.file_type})>"

# Создание сессии для работы с базой данных
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
session = SessionLocal()

# Функция для инициализации базы данных
def init_db():
    # Создание всех таблиц
    Base.metadata.create_all(bind=engine)
    print("База данных инициализирована.")

# Функция для закрытия сессии
def close_db():
    session.close()