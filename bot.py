import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import yadisk

from database import session, User, FileTemplate, LogSettings, init_db

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
yadisk_client = yadisk.YaDisk(token=os.getenv("YADISK_TOKEN"))

# Определение состояний для FSM
class RegistrationStates(StatesGroup):
    waiting_for_fullname = State()

class UploadStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_file_type = State()
    
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
        except Exception as e:
            logging.error(f"Ошибка при отправке лога в чат: {e}")

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
    folder_path = f"/FilesSendBot/{full_name}"
    try:
        if not yadisk_client.exists(folder_path):
            yadisk_client.mkdir(folder_path)
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
    
    file_type = callback.data.split(":")[1]  # essay или presentation
    user_id = callback.from_user.id
    user = session.query(User).filter(User.telegram_id == user_id).first()
    
    # Получаем данные о файле из состояния
    data = await state.get_data()
    file_id = data["file_id"]
    original_file_name = data["file_name"]
    
    # Определяем расширение файла
    _, file_ext = os.path.splitext(original_file_name)
    
    # Получаем шаблон имени файла из базы данных
    template = session.query(FileTemplate).first()
    if not template:
        template = FileTemplate()
        session.add(template)
        session.commit()
    
    # Формируем новое имя файла по шаблону
    current_date = datetime.now().strftime("%Y-%m-%d")
    file_type_name = "Эссе" if file_type == "essay" else "Презентация"
    
    # Разбиваем ФИО на части
    name_parts = user.full_name.split()
    surname = name_parts[0] if name_parts else ""
    
    # Заменяем плейсхолдеры в шаблоне
    new_file_name = template.template
    new_file_name = new_file_name.replace("[фамилия]", surname)
    new_file_name = new_file_name.replace("[тип]", file_type_name)
    new_file_name = f"{new_file_name}{file_ext}"
    
    # Путь для сохранения на Яндекс.Диске
    yadisk_path = f"/FilesSendBot/{user.full_name}/{new_file_name}"
    
    # Скачиваем файл
    file = await bot.get_file(file_id)
    file_path = file.file_path
    download_path = f"temp_{file_id}{file_ext}"
    await bot.download_file(file_path, download_path)
    
    try:
        # Загружаем файл на Яндекс.Диск
        yadisk_client.upload(download_path, yadisk_path)
        
        await callback.message.answer(
            f"Файл успешно загружен на Яндекс.Диск как {new_file_name}",
            reply_markup=get_main_menu(user.is_admin)
        )
        
        # Отправка лога о загрузке файла
        log_settings = session.query(LogSettings).first()
        if log_settings and log_settings.log_file_uploads:
            await send_log_message(
                f"📤 Загрузка файла: {user.full_name} (ID: {user_id})\n"
                f"Тип: {file_type_name}\n"
                f"Имя файла: {new_file_name}"
            )
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла на Яндекс.Диск: {e}")
        await callback.message.answer("Произошла ошибка при загрузке файла. Пожалуйста, попробуйте позже.")
    finally:
        # Удаляем временный файл
        if os.path.exists(download_path):
            os.remove(download_path)
    
    await state.clear()

# Обработчик для файлов неправильного формата
@router.message(UploadStates.waiting_for_file)
async def wrong_file(message: Message):
    await message.answer("Пожалуйста, отправьте файл (документ).")

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