import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from database import Database

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = "457081438"  # Ваш chat_id

# Проверка токена
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env файле")

# Инициализация базы данных
db = Database()

# Состояния пользователей
user_states = {}

# Барнаул часовой пояс (UTC+7)
BARNAUL_TZ = timedelta(hours=7)


def get_barnaul_time():
    """Получить текущее время в Барнаульском часовом поясе (UTC+7)"""
    return datetime.utcnow() + BARNAUL_TZ


def format_barnaul_time(dt=None):
    """Форматировать время в Барнаульском часовом поясе"""
    if dt is None:
        dt = get_barnaul_time()
    return dt.strftime('%d.%m.%Y %H:%M')


def get_empty_keyboard():
    """Получить пустую клавиатуру (скрыть кнопки)"""
    return ReplyKeyboardMarkup([[]], resize_keyboard=True)


def get_main_keyboard(user_id):
    """Получить основную клавиатуру с кнопками"""
    # Проверяем, является ли пользователь администратором
    is_admin = str(user_id) == ADMIN_CHAT_ID

    keyboard = [
        [KeyboardButton("📝 Заполнить анкету")],
    ]

    if is_admin:
        keyboard.append([
            KeyboardButton("📊 Получить отчет"),
            KeyboardButton("📢 Отправить напоминания")
        ])

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_next_saturday():
    """Получить следующую субботу в Барнаульском времени"""
    today = get_barnaul_time()
    days_ahead = 5 - today.weekday()  # 5 - суббота
    if days_ahead <= 0:  # Если сегодня суббота или позже
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def get_week_dates(start_date):
    """Получить даты недели начиная с понедельника после субботы"""
    dates = []
    monday = start_date + timedelta(days=2)
    for i in range(7):
        current_date = monday + timedelta(days=i)
        dates.append(current_date.strftime("%d.%m"))
    return dates


async def start_schedule_collection(context: ContextTypes.DEFAULT_TYPE):
    """Запуск сбора расписания в субботу"""
    # Отправляем напоминания в чат ПВЗ
    all_pvz = db.get_all_pvz()

    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz
        if chat_id:
            try:
                keyboard = [
                    [InlineKeyboardButton("📝 Заполнить анкету", url=f"https://t.me/{context.bot.username}?start=form")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                message_text = (
                    "📋 Напоминание!\n\n"
                    "Пора заполнить анкету расписания на следующую неделю.\n"
                    "Нажмите на кнопку ниже чтобы перейти к заполнению."
                )

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Напоминание отправлено в чат ПВЗ {pvz_name}")
            except Exception as e:
                logging.error(f"Ошибка отправки напоминания в чат {pvz_name}: {e}")


async def send_day_form(chat_id: int, day_index: int, context: ContextTypes.DEFAULT_TYPE):
    """Отправка формы для одного дня"""
    user = db.get_user(chat_id)
    if not user:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(chat_id)
        )
        return

    next_saturday = get_next_saturday()
    week_dates = get_week_dates(next_saturday)
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    if day_index >= len(week_dates):
        # Все дни заполнены
        user_schedule = db.get_user_schedule(chat_id, week_dates)
        filled_days = sum(1 for date in week_dates if date in user_schedule)

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Отлично! Вы заполнили расписание на {filled_days} из {len(week_dates)} дней!\n\n"
                 "Посмотреть свое расписание: /myschedule\n"
                 "Перезаполнить анкету: /form",
            reply_markup=get_main_keyboard(chat_id)
        )

        # Отправляем уведомление администратору
        user_info = db.get_user(chat_id)
        if user_info:
            # user_info структура: [0]id, [1]user_id, [2]username, [3]first_name, [4]pvz_id, [5]full_name, [6]pvz_name
            full_name = user_info[5] if user_info[5] else (user_info[3] or user_info[2] or f"User_{chat_id}")
            pvz_name = user_info[6]  # pvz_name находится в индексе 6

            admin_message = (
                f"📋 Новое заполненное расписание!\n\n"
                f"👤 Сотрудник: {full_name}\n"
                f"🏪 ПВЗ: {pvz_name}\n"
                f"📅 Заполнено дней: {filled_days}/{len(week_dates)}\n"
                f"🕒 Время заполнения: {format_barnaul_time()}"
            )

            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)
                logging.info(f"Уведомление отправлено администратору о заполнении анкеты сотрудником {full_name}")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления администратору: {e}")

        return

    date = week_dates[day_index]
    day_name = day_names[day_index]

    # Загружаем сохраненное расписание для этого дня
    saved_schedule = db.get_user_schedule(chat_id, [date])
    saved_time = saved_schedule.get(date, "")

    if day_index == 0:
        # Первый день - отправляем приветственное сообщение
        # user структура: [0]id, [1]user_id, [2]username, [3]first_name, [4]pvz_id, [5]full_name, [6]pvz_name
        pvz_name = user[6]  # pvz_name находится в индексе 6
        message_text = "📋 Заполните расписание на следующую неделю!\n\n"
        message_text += f"Период: {week_dates[0]} - {week_dates[-1]}\n"
        message_text += f"Ваш ПВЗ: {pvz_name}\n\n"
        message_text += "Заполняйте по одному дню:"
        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=get_main_keyboard(chat_id)
        )

    time_indicator = f" ✅ {saved_time}" if saved_time else ""

    keyboard = [
        [
            InlineKeyboardButton("9.00-15.00", callback_data=f"day_{day_index}_9-15"),
            InlineKeyboardButton("15.00-21.00", callback_data=f"day_{day_index}_15-21")
        ],
        [
            InlineKeyboardButton("Как нужно ПВЗ", callback_data=f"day_{day_index}_asneeded"),
            InlineKeyboardButton("Выходной", callback_data=f"day_{day_index}_dayoff")
        ],
        [
            InlineKeyboardButton("Точное время", callback_data=f"day_{day_index}_exact")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    day_message = f"{date} - {day_name}{time_indicator}"

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{day_message}\nВыберите вариант:",
        reply_markup=reply_markup
    )


async def show_start_time_selection(chat_id: int, day_index: int, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор времени начала смены с шагом 30 минут"""
    next_saturday = get_next_saturday()
    week_dates = get_week_dates(next_saturday)
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    date = week_dates[day_index]
    day_name = day_names[day_index]

    # Создаем кнопки с часами от 9:00 до 21:00 с шагом 30 минут
    keyboard = []
    row = []

    # Генерируем времена с шагом 30 минут
    times = []
    for hour in range(9, 22):  # с 9 до 21
        times.append(f"{hour}:00")
        if hour < 21:  # 21:30 не нужно
            times.append(f"{hour}:30")

    for time_str in times:
        row.append(InlineKeyboardButton(time_str, callback_data=f"start_{day_index}_{time_str}"))
        if len(row) == 3:  # 3 кнопки в ряд
            keyboard.append(row)
            row = []
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)

    # Кнопка отмены
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{day_index}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ {date} - {day_name}\n\nВыберите время начала смены:",
        reply_markup=reply_markup
    )


async def show_end_time_selection(chat_id: int, day_index: int, start_time: str, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор времени окончания смены с шагом 30 минут"""
    next_saturday = get_next_saturday()
    week_dates = get_week_dates(next_saturday)
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    date = week_dates[day_index]
    day_name = day_names[day_index]

    # Парсим время начала
    start_hour, start_minute = map(int, start_time.split(':'))
    start_total_minutes = start_hour * 60 + start_minute

    # Создаем кнопки с временами от start_time+30min до 21:00
    keyboard = []
    row = []

    # Генерируем времена с шагом 30 минут, начиная с start_time + 30min
    times = []
    current_minutes = start_total_minutes + 30  # начинаем с +30 минут

    while current_minutes <= 21 * 60:  # до 21:00 включительно
        hour = current_minutes // 60
        minute = current_minutes % 60
        time_str = f"{hour}:{minute:02d}"
        times.append(time_str)
        current_minutes += 30

    for time_str in times:
        row.append(InlineKeyboardButton(time_str, callback_data=f"end_{day_index}_{start_time}_{time_str}"))
        if len(row) == 3:  # 3 кнопки в ряд
            keyboard.append(row)
            row = []
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)

    # Кнопка отмены
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{day_index}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ {date} - {day_name}\nНачало: {start_time}\n\nВыберите время окончания смены:",
        reply_markup=reply_markup
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id

    # Проверяем, зарегистрирован ли пользователь
    existing_user = db.get_user(user_id)

    if existing_user:
        # Пользователь уже зарегистрирован
        welcome_text = (
            f"👋 С возвращением, {user.first_name}!\n\n"
            f"Ваш ПВЗ: {existing_user[6]}\n\n"
            "Используйте кнопки ниже для работы с ботом:"
        )
        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_keyboard(user_id)
        )

        # Если перешли по ссылке из напоминания
        if context.args and context.args[0] == 'form':
            await send_form(update, context)

    else:
        # Новый пользователь - просим ввести пароль (без кнопок)
        user_states[user_id] = {'state': 'waiting_password'}
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Для регистрации введите пароль вашего ПВЗ.\n"
            "Пароль можно получить у администратора.",
            reply_markup=get_empty_keyboard()  # Пустая клавиатура
        )


async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода пароля"""
    user = update.effective_user
    user_id = user.id
    password = update.message.text.strip()

    # Проверяем состояние пользователя
    if user_id not in user_states or user_states[user_id].get('state') != 'waiting_password':
        # Если пользователь не в состоянии ожидания пароля, игнорируем сообщение
        return

    # Проверяем пароль
    pvz = db.get_pvz_by_password(password)
    if pvz:
        # Переходим к вводу имени и фамилии (без кнопок)
        user_states[user_id] = {
            'state': 'waiting_full_name',
            'pvz_id': pvz[0],
            'pvz_name': pvz[1]
        }

        await update.message.reply_text(
            "✅ Пароль принят!\n\n"
            "Теперь введите ваше Имя и Фамилию:\n"
            "Например: Глеб Самарин",
            reply_markup=get_empty_keyboard()  # Пустая клавиатура
        )

    else:
        await update.message.reply_text(
            "❌ Неверный пароль.\n"
            "Пожалуйста, проверьте пароль и попробуйте еще раз.\n"
            "Пароль можно получить у администратора.",
            reply_markup=get_empty_keyboard()  # Пустая клавиатура
        )


async def handle_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода имени и фамилии"""
    user = update.effective_user
    user_id = user.id

    # Проверяем состояние пользователя
    if user_id not in user_states or user_states[user_id].get('state') != 'waiting_full_name':
        # Если пользователь не в состоянии ожидания имени, игнорируем сообщение
        return

    full_name = update.message.text.strip()

    # Проверяем, что введено хотя бы 2 слова (имя и фамилия)
    if len(full_name.split()) < 2:
        await update.message.reply_text(
            "❌ Пожалуйста, введите и Имя и Фамилию.\n"
            "Например: Глеб Самарин\n\n"
            "Попробуйте еще раз:",
            reply_markup=get_empty_keyboard()  # Пустая клавиатура
        )
        return

    # Регистрируем пользователя
    pvz_id = user_states[user_id]['pvz_id']
    pvz_name = user_states[user_id]['pvz_name']

    db.add_user(user_id, user.username, user.first_name, pvz_id, full_name)
    user_states[user_id] = {'state': 'registered'}

    await update.message.reply_text(
        f"✅ Регистрация успешна!\n\n"
        f"👤 Ваше имя: {full_name}\n"
        f"🏪 Ваш ПВЗ: {pvz_name}\n\n"
        "Теперь вы можете заполнить анкету расписания, нажав на кнопку ниже:",
        reply_markup=get_main_keyboard(user_id)  # Показываем кнопки только после регистрации
    )

    # Уведомляем администратора о новой регистрации
    admin_message = (
        f"👤 Новый сотрудник зарегистрировался!\n\n"
        f"Имя: {full_name}\n"
        f"ПВЗ: {pvz_name}\n"
        f"Время: {format_barnaul_time()}"
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)
    except Exception as e:
        logging.error(f"Ошибка отправки уведомления о регистрации: {e}")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (кнопок)"""
    user_id = update.effective_user.id
    text = update.message.text

    # Сначала проверяем, не находится ли пользователь в процессе регистрации
    if user_id in user_states:
        state = user_states[user_id].get('state')
        if state == 'waiting_password':
            await handle_password(update, context)
            return
        elif state == 'waiting_full_name':
            await handle_full_name(update, context)
            return

    # Если пользователь уже зарегистрирован, обрабатываем кнопки
    if text == "📝 Заполнить анкету":
        await send_form(update, context)
    elif text == "📊 Получить отчет":
        await manual_report(update, context)
    elif text == "📢 Отправить напоминания":
        await manual_collect(update, context)
    else:
        # Если сообщение не распознано как команда
        await update.message.reply_text(
            "Используйте кнопки ниже для работы с ботом:",
            reply_markup=get_main_keyboard(user_id)
        )


async def send_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка формы по команде /form"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    # Удаляем старое расписание пользователя при перезаполнении
    db.delete_user_schedule(user_id)

    # Начинаем заполнение с первого дня
    await send_day_form(user_id, 0, context)


async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data.startswith("day_"):
        parts = data.split("_")
        day_index = int(parts[1])
        time_type = parts[2]

        user = db.get_user(user_id)
        if not user:
            await query.edit_message_text("❌ Сначала зарегистрируйтесь с помощью /start")
            return

        next_saturday = get_next_saturday()
        week_dates = get_week_dates(next_saturday)
        day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        selected_date = week_dates[day_index]
        day_name = day_names[day_index]

        if time_type == "exact":
            # Показываем выбор времени начала смены
            await query.edit_message_text(
                text=f"⏰ {selected_date} - {day_name}\nВыбран вариант: Точное время"
            )
            await show_start_time_selection(user_id, day_index, context)

        else:
            # Сохраняем выбранный вариант
            time_mapping = {
                "9-15": "9.00-15.00",
                "15-21": "15.00-21.00",
                "asneeded": "Как нужно ПВЗ",
                "dayoff": "Выходной"
            }

            selected_time = time_mapping[time_type]
            db.save_schedule(user_id, selected_date, selected_time)

            await query.edit_message_text(
                text=f"✅ {selected_date} - {day_name}\nСохранено: {selected_time}"
            )

            # Отправляем следующий день
            await send_day_form(user_id, day_index + 1, context)

    elif data.startswith("start_"):
        # Пользователь выбрал время начала
        parts = data.split("_")
        day_index = int(parts[1])
        start_time = parts[2]  # теперь это строка типа "9:30"

        await query.edit_message_text(
            text=f"⏰ Выбрано начало: {start_time}"
        )
        await show_end_time_selection(user_id, day_index, start_time, context)

    elif data.startswith("end_"):
        # Пользователь выбрал время окончания
        parts = data.split("_")
        day_index = int(parts[1])
        start_time = parts[2]  # строка типа "9:30"
        end_time = parts[3]  # строка типа "18:00"

        next_saturday = get_next_saturday()
        week_dates = get_week_dates(next_saturday)
        day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        selected_date = week_dates[day_index]
        day_name = day_names[day_index]

        # Сохраняем выбранное время
        time_slot = f"{start_time}-{end_time}"
        db.save_schedule(user_id, selected_date, time_slot)

        await query.edit_message_text(
            text=f"✅ {selected_date} - {day_name}\nСохранено: {time_slot}"
        )

        # Отправляем следующий день
        await send_day_form(user_id, day_index + 1, context)

    elif data.startswith("cancel_"):
        # Отмена выбора времени
        day_index = int(data.split("_")[1])
        await query.edit_message_text("❌ Выбор времени отменен")
        await send_day_form(user_id, day_index, context)


async def send_admin_report(context: ContextTypes.DEFAULT_TYPE):
    """Отправка отчета администратору"""
    next_saturday = get_next_saturday()
    week_dates = get_week_dates(next_saturday)
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    all_pvz = db.get_all_pvz()

    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz

        report = f"📊 ОТЧЕТ ПО РАСПИСАНИЮ\nПВЗ: {pvz_name}\nПериод: {week_dates[0]} - {week_dates[-1]}\n\n"

        schedule_data = db.get_pvz_schedule_report(pvz_id, week_dates)

        # Группируем по дням
        day_schedule = {}
        for row in schedule_data:
            # row структура: [0]first_name, [1]username, [2]user_id, [3]date, [4]time_slot, [5]full_name
            # Используем полное имя из базы данных
            full_name = row[5] if row[5] else (row[0] or row[1] or f"User_{row[2]}")
            date = row[3]
            time_slot = row[4]

            if date not in day_schedule:
                day_schedule[date] = []
            day_schedule[date].append(f"{full_name} - {time_slot}")

        for i, date in enumerate(week_dates):
            report += f"📅 {date} - {day_names[i]}:\n"

            if date in day_schedule:
                for entry in day_schedule[date]:
                    report += f"  👤 {entry}\n"
            else:
                report += "  ❌ Нет данных\n"

            report += "\n"

        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=report)
            logging.info(f"Отчет для {pvz_name} отправлен администратору")
        except Exception as e:
            logging.error(f"Ошибка отправки отчета для {pvz_name}: {e}")


async def my_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать мое расписание"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    next_saturday = get_next_saturday()
    week_dates = get_week_dates(next_saturday)
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    schedule = db.get_user_schedule(user_id, week_dates)

    # user структура: [0]id, [1]user_id, [2]username, [3]first_name, [4]pvz_id, [5]full_name, [6]pvz_name
    pvz_name = user[6]
    text = f"📋 Ваше расписание на неделю:\nПВЗ: {pvz_name}\n\n"

    has_data = False
    for i, date in enumerate(week_dates):
        time_slot = schedule.get(date)
        if time_slot:
            has_data = True
            text += f"✅ {date} - {day_names[i]}: {time_slot}\n"
        else:
            text += f"❌ {date} - {day_names[i]}: Не заполнено\n"

    if has_data:
        text += "\nИзменить расписание: нажмите кнопку '📝 Заполнить анкету'"
    else:
        text += "\nЗаполнить расписание: нажмите кнопку '📝 Заполнить анкету'"

    await update.message.reply_text(
        text,
        reply_markup=get_main_keyboard(user_id)
    )


async def set_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить чат для напоминаний"""
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    # Проверяем, является ли пользователь администратором
    if str(user_id) != ADMIN_CHAT_ID:
        await update.message.reply_text(
            "❌ Только администратор может настраивать чат для напоминаний",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    pvz_id = user[4]
    chat_id = update.effective_chat.id

    db.set_pvz_chat_id(pvz_id, chat_id)

    await update.message.reply_text(
        f"✅ Чат настроен для получения напоминаний!\n"
        f"ПВЗ: {user[6]}\n"
        f"Chat ID: {chat_id}\n\n"
        f"Теперь бот будет отправлять сюда напоминания о заполнении анкет каждую субботу в 10:00 по Барнаулу.",
        reply_markup=get_main_keyboard(user_id)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка по командам"""
    help_text = (
        "📋 Бот для составления расписания\n\n"
        "Используйте кнопки ниже для работы:\n"
        "• 📝 Заполнить анкету - составить расписание на неделю\n"
        "• 📊 Получить отчет - для администратора\n"
        "• 📢 Отправить напоминания - для администратора\n\n"
        "Команды:\n"
        "/myschedule - посмотреть мое расписание\n"
        "/setchat - настроить чат для напоминаний (администратор)\n"
        "/help - эта справка"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=get_main_keyboard(update.effective_user.id)
    )


async def manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная отправка отчета"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text(
            "❌ У вас нет прав для этой команды.",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
        return

    await send_admin_report(context)
    await update.message.reply_text(
        "✅ Отчет отправлен!",
        reply_markup=get_main_keyboard(update.effective_user.id)
    )


async def manual_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручной запуск сбора данных"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text(
            "❌ У вас нет прав для этой команды.",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
        return

    await start_schedule_collection(context)
    await update.message.reply_text(
        "✅ Напоминания отправлены!",
        reply_markup=get_main_keyboard(update.effective_user.id)
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика"""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text(
            "❌ У вас нет прав для этой команды.",
            reply_markup=get_main_keyboard(update.effective_user.id)
        )
        return

    all_pvz = db.get_all_pvz()
    stats_text = "📈 Статистика бота:\n\n"

    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz

        # Получаем количество пользователей для этого ПВЗ
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE pvz_id = ?', (pvz_id,))
        user_count = cursor.fetchone()[0]

        # Получаем количество заполненных расписаний на эту неделю
        next_saturday = get_next_saturday()
        week_dates = get_week_dates(next_saturday)
        placeholders = ','.join('?' for _ in week_dates)
        cursor.execute(f'''
            SELECT COUNT(DISTINCT user_id) FROM schedule 
            WHERE date IN ({placeholders})
            AND user_id IN (SELECT user_id FROM users WHERE pvz_id = ?)
        ''', (*week_dates, pvz_id))
        filled_count = cursor.fetchone()[0]
        conn.close()

        stats_text += f"🏪 {pvz_name}:\n"
        stats_text += f"  👥 Сотрудников: {user_count}\n"
        stats_text += f"  📝 Заполнили анкету: {filled_count}\n"
        stats_text += f"  💬 Чат для напоминаний: {'✅' if chat_id else '❌'}\n\n"

    await update.message.reply_text(
        stats_text,
        reply_markup=get_main_keyboard(update.effective_user.id)
    )


async def set_commands(application: Application):
    """Установка команд меню"""
    commands = [
        BotCommand("start", "Начать работу"),
        BotCommand("form", "Заполнить анкету"),
        BotCommand("myschedule", "Мое расписание"),
        BotCommand("setchat", "Настроить чат для напоминаний (админ)"),
        BotCommand("help", "Помощь"),
    ]
    await application.bot.set_my_commands(commands)


def main():
    """Основная функция"""
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики в правильном порядке (от более специфичных к более общим)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("form", send_form))
    application.add_handler(CommandHandler("myschedule", my_schedule))
    application.add_handler(CommandHandler("setchat", set_chat))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("report", manual_report))
    application.add_handler(CommandHandler("collect", manual_collect))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(handle_button_click))

    # Обработчики текстовых сообщений в правильном порядке
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # Настраиваем планировщик задач с учетом часового пояса Барнаула
    job_queue = application.job_queue

    if job_queue:
        # Задача на субботу (каждую субботу в 10:00 по Барнаулу)
        # Учитываем что Railway работает в UTC, поэтому вычитаем 7 часов
        job_queue.run_daily(
            start_schedule_collection,
            time=datetime.strptime("03:00", "%H:%M").time(),  # 10:00 Барнаул - 7 часов = 03:00 UTC
            days=(5,)
        )

        # Задача на воскресенье (каждое воскресенье в 09:00 по Барнаулу)
        job_queue.run_daily(
            send_admin_report,
            time=datetime.strptime("02:00", "%H:%M").time(),  # 09:00 Барнаул - 7 часов = 02:00 UTC
            days=(6,)
        )

    # Устанавливаем команды меню
    application.post_init = set_commands

    # Запускаем бота
    logging.info("Бот запущен с часовым поясом Барнаул (UTC+7)...")
    print("Бот успешно запущен! Часовой пояс: Барнаул (UTC+7)")
    application.run_polling()


if __name__ == "__main__":

    main()
