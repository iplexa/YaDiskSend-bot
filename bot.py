import asyncio
import logging
import os
from datetime import datetime
import difflib
import requests
import json
import unicodedata

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram import exceptions as aiogram_exceptions
from urllib.parse import quote
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import yadisk

from database import session, User, FileTemplate, LogSettings, UploadedFile, init_db

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Инициализация Яндекс.Диска
try:
    yadisk_client = yadisk.YaDisk(token=os.getenv("YADISK_TOKEN"))
    # Проверяем токен
    if not yadisk_client.check_token():
        raise Exception("Invalid Yandex.Disk token")
    logging.info("Successfully connected to Yandex.Disk")
except Exception as e:
    logging.error(f"Failed to initialize Yandex.Disk client: {e}")
    raise

# API для проверки на антиплагиат
TEXT_RU_API_URL = "http://api.text.ru/post"

# Функция для проверки текста на антиплагиат
async def check_plagiarism(text):
    try:
        # Sanitize the input text
        sanitized_text = unicodedata.normalize('NFKC', text)
        sanitized_text = ''.join(c for c in sanitized_text if c.isprintable())

        # Отправляем текст на проверку
        payload = {
            "text": sanitized_text,
            "userkey": os.getenv("TEXT_RU_KEY")
        }
        response = requests.post(TEXT_RU_API_URL, json=payload)
        result = response.json()
        
        if 'text_uid' not in result:
            logging.error(f"Ошибка при отправке текста на проверку: {result.get('error_desc', 'Неизвестная ошибка')}")
            logging.error(f"Полный ответ API: {result}")  # Log the full API response
            logging.error(f"Sanitized text: {sanitized_text}")  # Log the sanitized text
            return None, []
            
        # Получаем результаты проверки
        check_payload = {
            "uid": result['text_uid'],
            "userkey": os.getenv("TEXT_RU_KEY"),
            "jsonvisible": "detail"
        }
        check_result = check_response.json()
        
        if 'error_code' in check_result:
            logging.error(f"Ошибка при получении результатов проверки: {check_result.get('error_desc', 'Неизвестная ошибка')}")
            logging.error(f"Полный ответ API: {check_result}")  # Log the full API response
            return None, []
            
        # Парсим результаты
        unique_percent = float(check_result.get('text_unique', 0))
        sources = []
        result_json = json.loads(check_result.get('result_json', '{}'))
        if 'urls' in result_json:
            sources = [{
                'url': url['url'],
                'plagiat': url['plagiat']
            } for url in result_json['urls']]
            
        return 100 - unique_percent, sources
    except Exception as e:
        logging.error(f"Ошибка при проверке на антиплагиат: {e}")
        return None, []

# Функция для сравнения текстов и получения процента схожести
def get_similarity_percentage(text1, text2):
    matcher = difflib.SequenceMatcher(None, text1, text2)
    return round(matcher.ratio() * 100, 2)

# Функция для проверки схожести с другими файлами
async def check_similarity(user_id, file_content, file_type):
    try:
        similar_files = []
        existing_files = session.query(UploadedFile).filter(UploadedFile.user_id != user_id, UploadedFile.file_type == file_type).all()
        
        # Устанавливаем тайм-аут в 5 секунд
        try:
            similar_files = await asyncio.wait_for(
                _check_similarity_internal(existing_files, file_content),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            logging.warning('Проверка схожести файлов превысила лимит времени')
            return []
        
        return similar_files
    except Exception as e:
        logging.error(f'Ошибка при проверке схожести: {e}')
        return []

async def _check_similarity_internal(existing_files, file_content):
    similar_files = []
    for file in existing_files:
        similarity = get_similarity_percentage(file_content, file.file_content)
        if similarity > 30:  # Порог схожести в 30%
            similar_files.append({
                'file_name': file.file_name,
                'similarity': similarity,
                'user_id': file.user_id
            })
    return similar_files

# Определение состояний для FSM
class RegistrationStates(StatesGroup):
    waiting_for_fullname = State()

class UploadStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_file_type = State()
    waiting_for_replace_confirmation = State()
    
    
class AdminStates(StatesGroup):
    waiting_for_admin_action = State()
    waiting_for_template = State()
    waiting_for_log_chat_id = State()
    waiting_for_user_management = State()
    waiting_for_user_id = State()

# Функция для отправки логов в чат
async def send_log_message(message_text):
    log_settings = session.query(LogSettings).first()
    if log_settings and log_settings.log_chat_id:
        try:
            await bot.send_message(chat_id=log_settings.log_chat_id, text=message_text)
        except aiogram_exceptions.TelegramBadRequest as e:
            logging.error(f"Ошибка при отправке лога в чат (неверный запрос): {str(e)}")
        except aiogram_exceptions.TelegramForbiddenError as e:
            logging.error(f"Ошибка при отправке лога в чат (доступ запрещен): {str(e)}")
        except Exception as e:
            logging.error(f"Неожиданная ошибка при отправке лога в чат: {str(e)}")
            logging.exception("Подробности ошибки:")

# Функция для создания главного меню
def get_main_menu(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.button(text="Загрузить файл", callback_data="menu:upload")
    if is_admin:
        builder.button(text="Админ-панель", callback_data="menu:admin")
    builder.adjust(1)
    return builder.as_markup()

# Функция для создания админ-меню
def get_admin_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="Управление пользователями", callback_data="admin:users")
    builder.button(text="Настройка шаблона файлов", callback_data="admin:template")
    builder.button(text="Настройка логирования", callback_data="admin:logging")
    builder.button(text="Назад", callback_data="menu:back")
    builder.adjust(1)
    return builder.as_markup()

# Обработчик команды /start
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверка, зарегистрирован ли пользователь
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    if user:
        await message.answer(
            f"Здравствуйте, {user.full_name}! Вы уже зарегистрированы.", 
            reply_markup=get_main_menu(user.is_admin)
        )
    else:
        await message.answer("Добро пожаловать! Для регистрации введите ваше ФИО.")
        await state.set_state(RegistrationStates.waiting_for_fullname)

# Обработчик ввода ФИО
@router.message(RegistrationStates.waiting_for_fullname)
async def process_fullname(message: Message, state: FSMContext):
    full_name = message.text.strip()
    
    if len(full_name.split()) < 2:
        await message.answer("Пожалуйста, введите полное ФИО (минимум имя и фамилия).")
        return
    
    user_id = message.from_user.id
    
    # Создание пользователя в БД
    new_user = User(telegram_id=user_id, full_name=full_name)
    session.add(new_user)
    session.commit()
    
    # Создание папки на Яндекс.Диске
    folder_path = f"/PKS12_SocialStudy/{full_name}"
    try:
        # Создаем базовую директорию
        base_path = "/PKS12_SocialStudy"
        max_retries = 3
        retry_delay = 2  # секунды между попытками
        
        logging.info(f"Начало создания директорий для пользователя {full_name} (ID: {user_id})")
        logging.info(f"Проверка существования базовой директории: {base_path}")

        for attempt in range(max_retries):
            try:
                if not yadisk_client.exists(base_path):
                    logging.info(f"Создание базовой директории: {base_path}")
                    yadisk_client.mkdir(base_path)
                    logging.info(f"Базовая директория успешно создана: {base_path}")
                else:
                    logging.info(f"Базовая директория уже существует: {base_path}")

                logging.info(f"Проверка существования пользовательской директории: {folder_path}")
                if not yadisk_client.exists(folder_path):
                    logging.info(f"Создание пользовательской директории: {folder_path}")
                    yadisk_client.mkdir(folder_path)
                    logging.info(f"Пользовательская директория успешно создана: {folder_path}")
                else:
                    logging.info(f"Пользовательская директория уже существует: {folder_path}")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    logging.error(f"Ошибка создания директории после {max_retries} попыток. Ошибка: {str(e)}")
                    logging.error(f"Стек вызовов: ", exc_info=True)
                    raise
                logging.warning(f"Ошибка при попытке {attempt + 1}: {str(e)}")
                logging.warning(f"Повторная попытка {attempt + 1} из {max_retries} через {retry_delay} секунд...")
                await asyncio.sleep(retry_delay)
        await message.answer(
            f"Регистрация успешна! Ваше ФИО: {full_name}", 
            reply_markup=get_main_menu(new_user.is_admin)
        )
        
        # Отправка лога о регистрации
        log_settings = session.query(LogSettings).first()
        if log_settings and log_settings.log_registrations:
            await send_log_message(f"🆕 Новая регистрация: {full_name} (ID: {user_id})")
    except Exception as e:
        logging.error(f"Ошибка при создании папки на Яндекс.Диске: {e}")
        await message.answer("Произошла ошибка при создании папки. Пожалуйста, попробуйте позже.")
    
    await state.clear()

# Обработчик меню
@router.callback_query(F.data.startswith("menu:"))
async def process_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    if action == "upload":
        await callback.message.answer("Пожалуйста, отправьте файл (эссе или презентацию).")
        await state.set_state(UploadStates.waiting_for_file)
    elif action == "admin" and user and user.is_admin:
        await callback.message.answer("Админ-панель:", reply_markup=get_admin_menu())
        await state.set_state(AdminStates.waiting_for_admin_action)
    elif action == "back":
        await callback.message.answer(
            f"Главное меню:", 
            reply_markup=get_main_menu(user and user.is_admin)
        )
        await state.clear()

# Обработчик команды /upload
@router.message(Command("upload"))
async def cmd_upload(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    if not user:
        await message.answer("Вы не зарегистрированы. Используйте команду /start для регистрации.")
        return
    
    await message.answer("Пожалуйста, отправьте файл (эссе или презентацию).")
    await state.set_state(UploadStates.waiting_for_file)

# Обработчик загрузки файла
@router.message(UploadStates.waiting_for_file, F.document)
async def process_file(message: Message, state: FSMContext):
    # Сохраняем информацию о файле в состоянии
    await state.update_data(file_id=message.document.file_id, file_name=message.document.file_name)
    
    # Создаем клавиатуру для выбора типа файла
    builder = InlineKeyboardBuilder()
    builder.button(text="Эссе", callback_data="file_type:essay")
    builder.button(text="Презентация", callback_data="file_type:presentation")
    
    await message.answer("Выберите тип файла:", reply_markup=builder.as_markup())
    await state.set_state(UploadStates.waiting_for_file_type)

# Обработчик выбора типа файла
@router.callback_query(UploadStates.waiting_for_file_type, F.data.startswith("file_type:"))
async def process_file_type(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    start_time = datetime.now()
    logging.info(f"[{start_time}] Начало обработки файла после выбора типа")
    
    file_type = callback.data.split(":")[1]  # essay или presentation
    user_id = callback.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    logging.info(f"[{datetime.now()}] Пользователь: {user.full_name} (ID: {user_id}), выбранный тип файла: {file_type}")
    
    # Получаем данные о файле из состояния
    data = await state.get_data()
    if "file_id" not in data:
        logging.warning(f"[{datetime.now()}] file_id not found in state data. Ensure that the file upload was successful.")
        await callback.message.answer("Ошибка: файл не был загружен. Пожалуйста, попробуйте снова.")
        await state.clear()
        return
    
    file_id = data["file_id"]
    original_file_name = data["file_name"]
    
    # Определяем расширение файла
    _, file_ext = os.path.splitext(original_file_name)
    logging.info(f"[{datetime.now()}] Оригинальное имя файла: {original_file_name}, расширение: {file_ext}")
    
    # Получаем шаблон имени файла из базы данных
    template = session.query(FileTemplate).first()
    if not template:
        template = FileTemplate()
        session.add(template)
        session.commit()
    
    # Формируем новое имя файла по шаблону
    current_date = datetime.now().strftime("%Y-%m-%d")
    file_type_name = "Эссе" if file_type == "essay" else "Презентация"
    logging.info(f"[{datetime.now()}] Формирование имени файла, тип: {file_type_name}")
    
    # Разбиваем ФИО на части
    name_parts = user.full_name.split()
    surname = name_parts[0] if name_parts else ""
    
    # Заменяем плейсхолдеры в шаблоне
    new_file_name = template.template
    new_file_name = new_file_name.replace("[фамилия]", surname)
    new_file_name = new_file_name.replace("[тип]", file_type_name)
    new_file_name = f"{new_file_name}{file_ext}"
    logging.info(f"[{datetime.now()}] Сформировано новое имя файла: {new_file_name}")
    
    # Путь для сохранения на Яндекс.Диске
    yadisk_path = f"/PKS12_SocialStudy/{user.full_name}/{new_file_name}"
    logging.info(f"[{datetime.now()}] Путь для сохранения на Яндекс.Диске: {yadisk_path}")
    
    # Создаем директорию, если она не существует
    try:
        folder_path = f"/PKS12_SocialStudy/{user.full_name}"
        logging.info(f"[{datetime.now()}] Проверка существования директорий")
        if not yadisk_client.exists("/PKS12_SocialStudy"):
            logging.info(f"[{datetime.now()}] Создание корневой директории /PKS12_SocialStudy")
            yadisk_client.mkdir("/PKS12_SocialStudy")
        
        if not yadisk_client.exists(folder_path):
            logging.info(f"[{datetime.now()}] Создание директории пользователя {folder_path}")
            try:
                yadisk_client.mkdir(folder_path)
                logging.info(f"[{datetime.now()}] Директория пользователя успешно создана: {folder_path}")
            except yadisk.exceptions.PathExistsError:
                logging.warning(f"[{datetime.now()}] Директория {folder_path} уже существует")
            except Exception as e:
                raise Exception(f"Failed to create user directory: {e}")
    except Exception as e:
        error_msg = f"Ошибка при создании папки на Яндекс.Диске: {str(e)}"
        logging.error(f"[{datetime.now()}] {error_msg}")
        await callback.message.answer("Произошла ошибка при создании папки. Пожалуйста, попробуйте позже.")
        if os.path.exists(download_path):
            os.remove(download_path)
        await state.clear()
        return
    
    # Скачиваем файл
    logging.info(f"[{datetime.now()}] Начало скачивания файла с Telegram серверов")
    file = await bot.get_file(file_id)
    file_path = file.file_path
    download_path = f"temp_{file_id}{file_ext}"
    await bot.download_file(file_path, download_path)
    logging.info(f"[{datetime.now()}] Файл успешно скачан во временный файл: {download_path}")
    
    # Проверяем, существует ли файл на Яндекс.Диске
    logging.info(f"[{datetime.now()}] Проверка существования файла на Яндекс.Диске: {yadisk_path}")
    if yadisk_client.exists(yadisk_path):
        builder = InlineKeyboardBuilder()
        builder.button(text="Да", callback_data="replace:yes")
        builder.button(text="Нет", callback_data="replace:no")
        logging.info(f"[{datetime.now()}] Файл {new_file_name} уже существует на Яндекс.Диске, запрос подтверждения замены")
        await callback.message.answer(
            f"Файл с именем {new_file_name} уже существует. Заменить его?",
            reply_markup=builder.as_markup()
        )
        await state.update_data(download_path=download_path, yadisk_path=yadisk_path, file_type_name=file_type_name)
        await state.set_state(UploadStates.waiting_for_replace_confirmation)
        return
    
    try:
        # Читаем содержимое файла для проверок
        logging.info(f"[{datetime.now()}] Начало чтения содержимого файла для проверок")
        try:
            with open(download_path, 'rb') as file:
                # Читаем бинарные данные и удаляем нулевые байты
                binary_content = file.read()
                binary_content = binary_content.replace(b'\x00', b'')
                logging.info(f"[{datetime.now()}] Файл прочитан, размер: {len(binary_content)} байт")
                # Пробуем декодировать в UTF-8
                try:
                    file_content = binary_content.decode('utf-8')
                    logging.info(f"[{datetime.now()}] Файл успешно декодирован в UTF-8")
                except UnicodeDecodeError:
                    # Если не удалось декодировать в UTF-8, пробуем другие кодировки
                    logging.info(f"[{datetime.now()}] Ошибка декодирования UTF-8, пробуем альтернативные кодировки")
                    for encoding in ['cp1251', 'latin1', 'iso-8859-1']:
                        try:
                            file_content = binary_content.decode(encoding)
                            logging.info(f"[{datetime.now()}] Файл успешно декодирован в кодировке {encoding}")
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        logging.warning(f"[{datetime.now()}] Не удалось декодировать файл ни в одной кодировке")
                        file_content = 'Содержимое файла не может быть прочитано'
        except Exception as e:
            logging.error(f"[{datetime.now()}] Ошибка при чтении файла: {str(e)}")
            file_content = 'Содержимое файла не может быть прочитано'
        
        # Проверяем схожесть с другими файлами только для эссе
        similar_files = []
        if file_type == 'essay':
            logging.info(f"[{datetime.now()}] Начало проверки схожести с другими файлами")
            similar_files = await check_similarity(user.id, file_content, file_type)
            if similar_files:
                logging.info(f"[{datetime.now()}] Найдено {len(similar_files)} похожих файлов")
            else:
                logging.info(f"[{datetime.now()}] Похожих файлов не найдено")
        else:
            logging.info(f"[{datetime.now()}] Проверка схожести пропущена для презентации")
        
        # Проверяем на антиплагиат, если это эссе
        plagiarism_result = None
        # if file_type == 'essay':
        #     plagiarism_percentage, sources = await check_plagiarism(file_content)
        #     if plagiarism_percentage is not None:
        #         plagiarism_result = {
        #             'percentage': plagiarism_percentage,
        #             'sources': sources
        #         }
        
        # Загружаем файл на Яндекс.Диск
        logging.info(f"[{datetime.now()}] Начало загрузки файла на Яндекс.Диск: {yadisk_path}")
        try:
            yadisk_client.upload(download_path, yadisk_path)
            logging.info(f"[{datetime.now()}] Файл успешно загружен на Яндекс.Диск")
        except UnicodeError as e:
            # Если возникла ошибка с кодировкой при загрузке
            logging.error(f"[{datetime.now()}] Ошибка кодировки при загрузке файла: {str(e)}")
            # Пробуем нормализовать имя файла
            normalized_path = unicodedata.normalize('NFKC', yadisk_path)
            logging.info(f"[{datetime.now()}] Попытка загрузки с нормализованным путем: {normalized_path}")
            yadisk_client.upload(download_path, normalized_path)
            yadisk_path = normalized_path
            logging.info(f"[{datetime.now()}] Файл успешно загружен с нормализованным путем")
        
        # Сохраняем информацию о файле в базе данных
        logging.info(f"[{datetime.now()}] Сохранение информации о файле в базе данных")
        uploaded_file = UploadedFile(
            user_id=user.id,
            file_name=new_file_name,
            file_type=file_type,
            file_content=file_content,
            file_path=yadisk_path
        )
        try:
            session.add(uploaded_file)
            session.commit()
            logging.info(f"[{datetime.now()}] Информация о файле успешно сохранена в базе данных")
        except Exception as e:
            session.rollback()
            logging.error(f"[{datetime.now()}] Ошибка при сохранении в базу данных: {str(e)}")
            raise
        
        # Формируем сообщение о результатах проверок
        result_message = f"Файл успешно загружен на Яндекс.Диск как {new_file_name}\n\n"
        
        # if similar_files:
        #     result_message += "⚠️ Обнаружены похожие файлы:\n"
        #     for file in similar_files:
        #         sim_user = session.query(User).filter(User.id == file['user_id']).first()
        #         result_message += f"- {file['file_name']} (схожесть: {file['similarity']}%, автор: {sim_user.full_name})\n"
        
        # if plagiarism_result:
        #     result_message += f"\n🔍 Результат проверки на антиплагиат:\n"
        #     result_message += f"Оригинальность: {100 - plagiarism_result['percentage']}%\n"
        #     if plagiarism_result['sources']:
        #         result_message += "Источники:\n"
        #         for source in plagiarism_result['sources'][:3]:  # Показываем только первые 3 источника
        #             result_message += f"- {source}\n"
        
        await callback.message.answer(result_message, reply_markup=get_main_menu(user.is_admin))
        
        # Отправка лога о загрузке файла и результатах проверок
        logging.info(f"[{datetime.now()}] Подготовка сообщения для отправки в лог-чат")
        log_settings = session.query(LogSettings).first()
        if log_settings and log_settings.log_file_uploads:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_message = f"📤 Загрузка файла: {user.full_name} (ID: {user_id})\n"
            log_message += f"Время: {current_time}\n"
            log_message += f"Тип: {file_type_name}\n"
            log_message += f"Имя файла: {new_file_name}\n"
            
            if similar_files:
                log_message += "\n⚠️ Обнаружены похожие файлы!\n"
                for file in similar_files:
                    sim_user = session.query(User).filter(User.id == file['user_id']).first()
                    log_message += f"- {file['file_name']} (схожесть: {file['similarity']}%, автор: {sim_user.full_name})\n"
            
            if plagiarism_result:
                log_message += f"\n🔍 Оригинальность: Функция в разработке."
            
            logging.info(f"[{datetime.now()}] Отправка сообщения в лог-чат")
            await send_log_message(log_message)
            logging.info(f"[{datetime.now()}] Сообщение успешно отправлено в лог-чат")
    except Exception as e:
        logging.error(f"[{datetime.now()}] Ошибка при загрузке файла на Яндекс.Диске: {e}")
        await callback.message.answer("Произошла ошибка при загрузке файла. Пожалуйста, попробуйте позже.")
    finally:
        # Удаляем временный файл
        if os.path.exists(download_path):
            logging.info(f"[{datetime.now()}] Удаление временного файла: {download_path}")
            os.remove(download_path)
            logging.info(f"[{datetime.now()}] Временный файл успешно удален")
        
        # Вычисляем общее время выполнения
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        logging.info(f"[{end_time}] Завершение обработки файла. Общее время выполнения: {execution_time} секунд")
    
    await state.clear()

# Обработчик для файлов неправильного формата
@router.message(UploadStates.waiting_for_file)
async def wrong_file(message: Message):
    await message.answer("Пожалуйста, отправьте файл (документ).")

@router.callback_query(UploadStates.waiting_for_replace_confirmation, F.data.startswith("replace:"))
async def process_replace_confirmation(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    start_time = datetime.now()
    logging.info(f"[{start_time}] Начало обработки подтверждения замены файла")
    
    choice = callback.data.split(":")[1]
    logging.info(f"[{datetime.now()}] Выбор пользователя: {choice}")
    
    data = await state.get_data()
    download_path = data.get("download_path")
    yadisk_path = data.get("yadisk_path")
    file_type_name = data.get("file_type_name")
    file_type = "essay" if file_type_name == "Эссе" else "presentation"
    user_id = callback.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    logging.info(f"[{datetime.now()}] Пользователь: {user.full_name} (ID: {user_id}), тип файла: {file_type_name}, путь: {yadisk_path}")
    
    if choice == "yes":
        try:
            logging.info(f"[{datetime.now()}] Начало процесса замены файла")
            # Читаем содержимое файла для проверок
            logging.info(f"[{datetime.now()}] Начало чтения содержимого файла для проверок: {download_path}")
            try:
                with open(download_path, 'rb') as file:
                    binary_content = file.read()
                    binary_content = binary_content.replace(b'\x00', b'')
                    logging.info(f"[{datetime.now()}] Файл прочитан, размер: {len(binary_content)} байт")
                    try:
                        file_content = binary_content.decode('utf-8')
                        logging.info(f"[{datetime.now()}] Файл успешно декодирован в UTF-8")
                    except UnicodeDecodeError:
                        logging.info(f"[{datetime.now()}] Ошибка декодирования UTF-8, пробуем альтернативные кодировки")
                        for encoding in ['cp1251', 'latin1', 'iso-8859-1']:
                            try:
                                file_content = binary_content.decode(encoding)
                                logging.info(f"[{datetime.now()}] Файл успешно декодирован в кодировке {encoding}")
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            logging.warning(f"[{datetime.now()}] Не удалось декодировать файл ни в одной кодировке")
                            file_content = 'Содержимое файла не может быть прочитано'
            except Exception as e:
                logging.error(f"[{datetime.now()}] Ошибка при чтении файла: {str(e)}")
                file_content = 'Содержимое файла не может быть прочитано'

            # Проверяем схожесть с другими файлами только для эссе
            similar_files = []
            if file_type == 'essay':
                logging.info(f"[{datetime.now()}] Начало проверки схожести с другими файлами")
                similar_files = await check_similarity(user.id, file_content, file_type)
                if similar_files:
                    logging.info(f"[{datetime.now()}] Найдено {len(similar_files)} похожих файлов")
                else:
                    logging.info(f"[{datetime.now()}] Похожих файлов не найдено")
            else:
                logging.info(f"[{datetime.now()}] Проверка схожести пропущена для презентации")

            # Проверяем на антиплагиат, если это эссе
            plagiarism_result = None
            # if file_type == 'essay':
            #     plagiarism_percentage, sources = await check_plagiarism(file_content)
            #     if plagiarism_percentage is not None:
            #         plagiarism_result = {
            #             'percentage': plagiarism_percentage,
            #             'sources': sources
            #         }

            # Загружаем файл на Яндекс.Диск
            logging.info(f"[{datetime.now()}] Начало загрузки файла на Яндекс.Диск с перезаписью: {yadisk_path}")
            yadisk_client.upload(download_path, yadisk_path, overwrite=True)
            logging.info(f"[{datetime.now()}] Файл успешно загружен на Яндекс.Диск")

            # Обновляем информацию о файле в базе данных
            logging.info(f"[{datetime.now()}] Обновление информации о файле в базе данных")
            existing_file = session.query(UploadedFile).filter(
                UploadedFile.user_id == user.id,
                UploadedFile.file_path == yadisk_path
            ).first()

            if existing_file:
                logging.info(f"[{datetime.now()}] Найдена существующая запись в БД, обновление содержимого")
                existing_file.file_content = file_content
                session.commit()
                logging.info(f"[{datetime.now()}] Запись в БД успешно обновлена")
            else:
                logging.info(f"[{datetime.now()}] Создание новой записи в БД")
                uploaded_file = UploadedFile(
                    user_id=user.id,
                    file_name=os.path.basename(yadisk_path),
                    file_type=file_type,
                    file_content=file_content,
                    file_path=yadisk_path
                )
                session.add(uploaded_file)
                session.commit()
                logging.info(f"[{datetime.now()}] Новая запись в БД успешно создана")

            # Формируем сообщение о результатах проверок
            result_message = f"Файл успешно заменен на Яндекс.Диске как {os.path.basename(yadisk_path)}\n\n"

            # if similar_files:
            #     result_message += "⚠️ Обнаружены похожие файлы:\n"
            #     for file in similar_files:
            #         similar_user = session.query(User).filter(User.id == file['user_id']).first()
            #         result_message += f"- {file['file_name']} (схожесть: {file['similarity']}%, автор: {similar_user.full_name})\n"

            # if plagiarism_result:
            #     result_message += f"\n🔍 Результат проверки на антиплагиат:\n"
            #     result_message += f"Оригинальность: {100 - plagiarism_result['percentage']}%\n"
            #     if plagiarism_result['sources']:
            #         result_message += "Источники:\n"
            #         for source in plagiarism_result['sources'][:3]:
            #             result_message += f"- {source['url']} (совпадение: {source['plagiat']}%)\n"

            await callback.message.answer(result_message, reply_markup=get_main_menu(user.is_admin))
            
            # Отправка лога о загрузке файла
            logging.info(f"[{datetime.now()}] Подготовка сообщения для отправки в лог-чат")
            log_settings = session.query(LogSettings).first()
            if log_settings and log_settings.log_file_uploads:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_message = f"📤 Замена файла: {user.full_name} (ID: {user_id})\n"
                log_message += f"Время: {current_time}\n"
                log_message += f"Тип: {file_type_name}\n"
                log_message += f"Имя файла: {os.path.basename(yadisk_path)}\n"

                if similar_files:
                    log_message += "\n⚠️ Обнаружены похожие файлы!\n"
                    for file in similar_files:
                        similar_user = session.query(User).filter(User.id == file['user_id']).first()
                        log_message += f"- {file['file_name']} (схожесть: {file['similarity']}%, автор: {similar_user.full_name})\n"

                
                log_message += f"\n🔍 Оригинальность: функция в разработке."

                logging.info(f"[{datetime.now()}] Отправка сообщения в лог-чат")
            await send_log_message(log_message)
            logging.info(f"[{datetime.now()}] Сообщение успешно отправлено в лог-чат")

        except Exception as e:
            logging.error(f"[{datetime.now()}] Ошибка при замене файла на Яндекс.Диске: {e}")
            await callback.message.answer("Произошла ошибка при замене файла. Пожалуйста, попробуйте позже.")
    else:
        logging.info(f"[{datetime.now()}] Пользователь отменил замену файла")
        await callback.message.answer("Загрузка файла отменена.", reply_markup=get_main_menu(user.is_admin))
    
    # Удаляем временный файл
    if download_path and os.path.exists(download_path):
        logging.info(f"[{datetime.now()}] Удаление временного файла: {download_path}")
        os.remove(download_path)
        logging.info(f"[{datetime.now()}] Временный файл успешно удален")
    
    # Вычисляем общее время выполнения
    end_time = datetime.now()
    execution_time = (end_time - start_time).total_seconds()
    logging.info(f"[{end_time}] Завершение обработки замены файла. Общее время выполнения: {execution_time} секунд")
    
    await state.clear()

# Обработчик админ-меню
@router.callback_query(AdminStates.waiting_for_admin_action, F.data.startswith("admin:"))
async def process_admin_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    if not user or not user.is_admin:
        await callback.message.answer("У вас нет прав администратора.")
        await state.clear()
        return
    
    if action == "users":
        # Получаем список пользователей
        users = session.query(User).all()
        user_list = "\n".join([f"{u.id}. {u.full_name} (ID: {u.telegram_id}, Админ: {'Да' if u.is_admin else 'Нет'})" for u in users])
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Назначить админа", callback_data="user_action:make_admin")
        builder.button(text="Удалить админа", callback_data="user_action:remove_admin")
        builder.button(text="Назад", callback_data="admin:back")
        builder.adjust(1)
        
        await callback.message.answer(f"Список пользователей:\n{user_list}", reply_markup=builder.as_markup())
        await state.set_state(AdminStates.waiting_for_user_management)
    
    elif action == "template":
        template = session.query(FileTemplate).first()
        if not template:
            template = FileTemplate()
            session.add(template)
            session.commit()
        
        await callback.message.answer(
            f"Текущий шаблон имени файла: {template.template}\n\n"
            f"Доступные плейсхолдеры:\n"
            f"[фамилия] - фамилия пользователя\n"
            f"[тип] - тип файла (Эссе/Презентация)\n\n"
            f"Введите новый шаблон:"
        )
        await state.set_state(AdminStates.waiting_for_template)
    
    elif action == "logging":
        log_settings = session.query(LogSettings).first()
        if not log_settings:
            log_settings = LogSettings()
            session.add(log_settings)
            session.commit()
        
        builder = InlineKeyboardBuilder()
        builder.button(
            text=f"Логирование регистраций: {'Вкл' if log_settings.log_registrations else 'Выкл'}", 
            callback_data="log_action:toggle_reg"
        )
        builder.button(
            text=f"Логирование загрузок: {'Вкл' if log_settings.log_file_uploads else 'Выкл'}", 
            callback_data="log_action:toggle_upload"
        )
        builder.button(text="Изменить ID чата", callback_data="log_action:set_chat")
        builder.button(text="Назад", callback_data="admin:back")
        builder.adjust(1)
        
        await callback.message.answer(
            f"Настройки логирования:\n"
            f"ID чата для логов: {log_settings.log_chat_id or 'Не установлен'}",
            reply_markup=builder.as_markup()
        )
    
    elif action == "back":
        await callback.message.answer("Главное меню:", reply_markup=get_main_menu(True))
        await state.clear()

# Обработчик настройки шаблона
@router.message(AdminStates.waiting_for_template)
async def process_template(message: Message, state: FSMContext):
    new_template = message.text.strip()
    
    if not new_template:
        await message.answer("Шаблон не может быть пустым. Введите шаблон снова:")
        return
    
    template = session.query(FileTemplate).first()
    if not template:
        template = FileTemplate(template=new_template)
        session.add(template)
    else:
        template.template = new_template
    
    session.commit()
    
    await message.answer(
        f"Шаблон успешно обновлен: {new_template}", 
        reply_markup=get_admin_menu()
    )
    await state.set_state(AdminStates.waiting_for_admin_action)

# Обработчик настройки логирования
@router.callback_query(F.data.startswith("log_action:"))
async def process_log_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    
    log_settings = session.query(LogSettings).first()
    if not log_settings:
        log_settings = LogSettings()
        session.add(log_settings)
        session.commit()
    
    if action == "toggle_reg":
        log_settings.log_registrations = not log_settings.log_registrations
        session.commit()
        await process_admin_action(callback, state)
    
    elif action == "toggle_upload":
        log_settings.log_file_uploads = not log_settings.log_file_uploads
        session.commit()
        await process_admin_action(callback, state)
    
    elif action == "set_chat":
        await callback.message.answer(
            "Введите ID чата для логирования (или 'clear' для удаления):"
        )
        await state.set_state(AdminStates.waiting_for_log_chat_id)

# Обработчик ввода ID чата для логов
@router.message(AdminStates.waiting_for_log_chat_id)
async def process_log_chat_id(message: Message, state: FSMContext):
    chat_id = message.text.strip()
    
    log_settings = session.query(LogSettings).first()
    if not log_settings:
        log_settings = LogSettings()
        session.add(log_settings)
    
    if chat_id.lower() == "clear":
        log_settings.log_chat_id = None
        session.commit()
        await message.answer("ID чата для логов удален.", reply_markup=get_admin_menu())
    else:
        log_settings.log_chat_id = chat_id
        session.commit()
        await message.answer(f"ID чата для логов установлен: {chat_id}", reply_markup=get_admin_menu())
    
    await state.set_state(AdminStates.waiting_for_admin_action)

# Обработчик управления пользователями
@router.callback_query(AdminStates.waiting_for_user_management, F.data.startswith("user_action:"))
async def process_user_action(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    
    await state.update_data(user_action=action)
    await callback.message.answer("Введите ID пользователя:")
    await state.set_state(AdminStates.waiting_for_user_id)

@router.callback_query(AdminStates.waiting_for_user_management, F.data == "admin:back")
async def process_user_list_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Админ-панель:", reply_markup=get_admin_menu())
    await state.set_state(AdminStates.waiting_for_admin_action)

@router.callback_query(F.data == "admin:back")
async def process_admin_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Админ-панель:", reply_markup=get_admin_menu())
    await state.set_state(AdminStates.waiting_for_admin_action)

# Обработчик ввода ID пользователя
@router.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Некорректный ID. Введите числовой ID:")
        return
    
    data = await state.get_data()
    action = data.get("user_action")
    
    user = session.query(User).filter(User.telegram_id == user_id).first()
    if not user:
        await message.answer("Пользователь не найден. Проверьте ID и попробуйте снова.")
        return
    
    if action == "make_admin":
        user.is_admin = True
        session.commit()
        await message.answer(f"Пользователь {user.full_name} назначен администратором.", reply_markup=get_admin_menu())
    elif action == "remove_admin":
        user.is_admin = False
        session.commit()
        await message.answer(f"Пользователь {user.full_name} больше не администратор.", reply_markup=get_admin_menu())
    
    await state.set_state(AdminStates.waiting_for_admin_action)

# Команда для назначения первого администратора
@router.message(Command("makeadmin"))
async def cmd_make_admin(message: Message):
    user_id = message.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    # Проверяем, есть ли уже администраторы
    admin_exists = session.query(User).filter(User.is_admin == True).first()
    
    if not admin_exists:
        if user:
            user.is_admin = True
            session.commit()
            await message.answer("Вы назначены первым администратором системы.")
        else:
            await message.answer("Вы не зарегистрированы. Используйте команду /start для регистрации.")
    else:
        await message.answer("Администратор уже существует. Только текущий администратор может назначать новых.")

# Запуск бота
async def main():
    # Инициализация базы данных
    init_db()
    
    # Создаем настройки логирования, если их нет
    log_settings = session.query(LogSettings).first()
    if not log_settings:
        log_settings = LogSettings()
        session.add(log_settings)
    
    # Создаем шаблон имени файла, если его нет
    template = session.query(FileTemplate).first()
    if not template:
        template = FileTemplate()
        session.add(template)
    
    session.commit()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())