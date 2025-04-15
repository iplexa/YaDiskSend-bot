# FilesSendBot

Телеграм-бот для регистрации пользователей и загрузки файлов на Яндекс.Диск.

## Функциональность

1. Регистрация пользователей с сохранением ФИО
2. Создание папки на Яндекс.Диске для каждого пользователя
3. Загрузка файлов (эссе или презентаций) с автоматическим переименованием

## Установка и настройка

### Предварительные требования

- Python 3.8 или выше
- PostgreSQL
- Токен Telegram бота (получить у @BotFather)
- Токен Яндекс.Диска (получить в [кабинете разработчика](https://yandex.ru/dev/disk/poligon/))

### Шаги установки

1. Клонировать репозиторий:

```bash
git clone https://github.com/iplexa/FilesSendBot.git
cd FilesSendBot
```

2. Установить зависимости:

```bash
pip install -r requirements.txt
```

3. Создать файл .env на основе .env.example и заполнить его своими данными:

```bash
cp .env.example .env
# Отредактируйте файл .env, добавив свои токены и настройки БД
```

4. Создать базу данных в PostgreSQL:

```bash
psql -U postgres
CREATE DATABASE files_send_bot;
\q
```

### Запуск бота

```bash
python bot.py
```

## Использование

### Для пользователей:
1. Начните диалог с ботом командой `/start`
2. Следуйте инструкциям для регистрации (введите ФИО)
3. Используйте кнопку "Загрузить файл" или команду `/upload` для загрузки файлов
4. Выберите тип файла (эссе или презентация)
5. Файл будет загружен на Яндекс.Диск в папку с вашим ФИО с новым именем по шаблону

### Для администраторов:
1. Используйте команду `/makeadmin` для назначения первого администратора
2. В главном меню нажмите кнопку "Админ-панель"
3. В админ-панели доступны следующие функции:
   - Управление пользователями (назначение/удаление администраторов)
   - Настройка шаблона имени файла (по умолчанию: [фамилия]_ПКС12_[тип])
   - Настройка логирования (включение/отключение логов, настройка ID чата для логов)

## Деплой с использованием Docker

### Подготовка к деплою

1. Создайте файл `.env` на основе `.env.example` и заполните его своими данными:

```bash
cp .env.example .env
# Отредактируйте файл .env, добавив свои токены
```

2. Заполните параметры подключения к вашей PostgreSQL в файле `.env`:

```
DB_HOST=ваш_хост
DB_PORT=ваш_порт
DB_NAME=ваша_бд
DB_USER=ваш_пользователь
DB_PASSWORD=ваш_пароль
```

### Деплой в тестовом режиме

```bash
# Сборка и запуск контейнеров
docker-compose up --build

# Запуск в фоновом режиме
docker-compose up -d

# Просмотр логов
docker-compose logs -f
```

### Деплой в продакшн режиме

1. Создайте файл `docker-compose.prod.yml` с дополнительными настройками безопасности:

```yaml
version: '3.8'

services:
  bot:
    build: .
    restart: always
    depends_on:
      - postgres
    env_file:
      - .env
    volumes:
      - ./tmp:/app/tmp
    deploy:
      resources:
        limits:
          memory: 512M
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  postgres:
    image: postgres:14-alpine
    restart: always
    environment:
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_USER=postgres
      - POSTGRES_DB=files_send_bot
    volumes:
      - postgres_data:/var/lib/postgresql/data
    deploy:
      resources:
        limits:
          memory: 1G
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  postgres_data:
```

2. Запустите в продакшн режиме:

```bash
docker-compose -f docker-compose.prod.yml up -d
```

3. Проверка статуса:

```bash
docker-compose -f docker-compose.prod.yml ps
```

### Обновление бота в продакшн

```bash
# Остановка контейнеров
docker-compose -f docker-compose.prod.yml down

# Получение обновлений
git pull

# Перезапуск с обновленным кодом
docker-compose -f docker-compose.prod.yml up --build -d
```

## Структура проекта

- `bot.py` - основной файл бота
- `database.py` - модели и функции для работы с базой данных
- `requirements.txt` - зависимости проекта
- `.env` - файл с переменными окружения (не включен в репозиторий)
- `.env.example` - пример файла с переменными окружения
- `Dockerfile` - инструкции для сборки Docker-образа
- `docker-compose.yml` - конфигурация для запуска в Docker