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

def is_private_chat(update: Update) -> bool:
    """Проверяем, что сообщение из приватного чата"""
    return update.effective_chat.type == 'private'

def get_barnaul_time():
    """Получить текущее время в Барнаульском часовом поясе (UTC+7)"""
    return datetime.utcnow() + BARNAUL_TZ

def format_barnaul_time(dt=None):
    """Форматировать время в Барнаульском часовом поясе"""
    if dt is None:
        dt = get_barnaul_time()
    return dt.strftime('%d.%m.%Y %H:%M')

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
    monday = start_date + timedelta(days=2)  # Понедельник после субботы
    for i in range(7):
        current_date = monday + timedelta(days=i)
        dates.append(current_date.strftime("%d.%m"))
    return dates

def get_target_week_dates():
    """Получить даты целевой недели (неделя после следующей субботы)"""
    next_saturday = get_next_saturday()
    return get_week_dates(next_saturday)

async def start_schedule_collection(context: ContextTypes.DEFAULT_TYPE):
    """Субботнее напоминание - обычное (в 9:00 по Барнаулу)"""
    all_pvz = db.get_all_pvz()
    
    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz
        if chat_id:
            try:
                keyboard = [
                    [InlineKeyboardButton("📝 Заполнить анкету", url=f"https://t.me/{context.bot.username}?start=form")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                target_week_dates = get_target_week_dates()
                
                message_text = (
                    "📋 Субботнее напоминание!\n\n"
                    f"Пора заполнить анкету расписания на неделю {target_week_dates[0]} - {target_week_dates[-1]}.\n"
                    "Нажмите на кнопку ниже чтобы перейти к заполнению."
                )
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Субботнее напоминание отправлено в чат ПВЗ {pvz_name}")
            except Exception as e:
                logging.error(f"Ошибка отправки субботнего напоминания в чат {pvz_name}: {e}")

async def send_sunday_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Воскресное напоминание - отмечает тех, кто не заполнил (в 9:00 по Барнаулу)"""
    target_week_dates = get_target_week_dates()
    
    all_pvz = db.get_all_pvz()
    
    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz
        if not chat_id:
            continue
            
        try:
            # Получаем всех пользователей этого ПВЗ
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, username, first_name, full_name 
                FROM users 
                WHERE pvz_id = ?
            ''', (pvz_id,))
            all_users = cursor.fetchall()
            
            # Получаем пользователей, которые уже заполнили расписание
            placeholders = ','.join('?' for _ in target_week_dates)
            cursor.execute(f'''
                SELECT DISTINCT user_id 
                FROM schedule 
                WHERE date IN ({placeholders})
                AND user_id IN (SELECT user_id FROM users WHERE pvz_id = ?)
            ''', (*target_week_dates, pvz_id))
            filled_users = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            # Находим пользователей, которые НЕ заполнили расписание
            not_filled_users = []
            for user in all_users:
                user_id, username, first_name, full_name = user
                # Пропускаем администратора
                if str(user_id) == ADMIN_CHAT_ID:
                    continue
                    
                if user_id not in filled_users:
                    display_name = full_name or first_name or username or f"User_{user_id}"
                    not_filled_users.append(display_name)
            
            if not_filled_users:
                # Формируем сообщение с упоминаниями
                message_text = "📢 Воскресное напоминание!\n\n"
                message_text += f"Следующие сотрудники еще не заполнили расписание на неделю {target_week_dates[0]} - {target_week_dates[-1]}:\n\n"
                
                for i, user_name in enumerate(not_filled_users, 1):
                    message_text += f"{i}. {user_name}\n"
                
                message_text += "\nПожалуйста, заполните расписание до начала недели!"
                
                keyboard = [
                    [InlineKeyboardButton("📝 Заполнить анкету", url=f"https://t.me/{context.bot.username}?start=form")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=reply_markup
                )
                logging.info(f"Воскресное напоминание отправлено в чат ПВЗ {pvz_name}. Не заполнили: {len(not_filled_users)} чел.")
            else:
                # Все заполнили - отправляем позитивное сообщение
                message_text = "✅ Отличная работа!\n\n"
                message_text += f"Все сотрудники заполнили расписание на неделю {target_week_dates[0]} - {target_week_dates[-1]}!\n"
                message_text += "Спасибо за своевременное заполнение!"
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text
                )
                logging.info(f"Все сотрудники ПВЗ {pvz_name} заполнили расписание")
            
        except Exception as e:
            logging.error(f"Ошибка отправки воскресного напоминания в чат {pvz_name}: {e}")

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
    
    target_week_dates = get_target_week_dates()
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    if day_index >= len(target_week_dates):
        # Все дни заполнены
        user_schedule = db.get_user_schedule(chat_id, target_week_dates)
        filled_days = sum(1 for date in target_week_dates if date in user_schedule)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Отлично! Вы заполнили расписание на {filled_days} из {len(target_week_dates)} дней!\n\n"
                 f"Период: {target_week_dates[0]} - {target_week_dates[-1]}\n\n"
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
                f"📅 Период: {target_week_dates[0]} - {target_week_dates[-1]}\n"
                f"✅ Заполнено дней: {filled_days}/{len(target_week_dates)}\n"
                f"🕒 Время заполнения: {format_barnaul_time()}"
            )
            
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)
                logging.info(f"Уведомление отправлено администратору о заполнении анкеты сотрудником {full_name}")
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления администратору: {e}")
        
        return
    
    date = target_week_dates[day_index]
    day_name = day_names[day_index]
    
    # Загружаем сохраненное расписание для этого дня
    saved_schedule = db.get_user_schedule(chat_id, [date])
    saved_time = saved_schedule.get(date, "")
    
    if day_index == 0:
        # Первый день - отправляем приветственное сообщение
        # user структура: [0]id, [1]user_id, [2]username, [3]first_name, [4]pvz_id, [5]full_name, [6]pvz_name
        pvz_name = user[6]  # pvz_name находится в индексе 6
        message_text = "📋 Заполните расписание на следующую неделю!\n\n"
        message_text += f"Период: {target_week_dates[0]} - {target_week_dates[-1]}\n"
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
    """Показать выбор времени начала смены"""
    target_week_dates = get_target_week_dates()
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    date = target_week_dates[day_index]
    day_name = day_names[day_index]
    
    # Создаем кнопки с часами от 9:00 до 21:00 с шагом 30 минут
    keyboard = []
    row = []
    
    # Генерируем времена с 9:00 до 21:00 с шагом 30 минут
    times = []
    for hour in range(9, 22):  # с 9 до 21
        times.append(f"{hour}:00")
        if hour < 21:  # 21:30 не добавляем, так как конец дня в 21:00
            times.append(f"{hour}:30")
    
    for time_str in times:
        hour, minute = map(int, time_str.split(':'))
        callback_data = f"start_{day_index}_{hour}_{minute}"
        row.append(InlineKeyboardButton(time_str, callback_data=callback_data))
        
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

async def show_end_time_selection(chat_id: int, day_index: int, start_hour: int, start_minute: int, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор времени окончания смены"""
    target_week_dates = get_target_week_dates()
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    date = target_week_dates[day_index]
    day_name = day_names[day_index]
    
    # Вычисляем общее количество минут для начала смены
    start_total_minutes = start_hour * 60 + start_minute
    
    # Создаем кнопки с временами окончания (после начала смены)
    keyboard = []
    row = []
    
    # Генерируем времена с 9:00 до 21:00 с шагом 30 минут
    times = []
    for hour in range(9, 22):  # с 9 до 21
        for minute in [0, 30]:
            if hour == 21 and minute == 30:  # 21:30 не добавляем
                continue
            time_total_minutes = hour * 60 + minute
            # Показываем только времена, которые после начала смены
            if time_total_minutes > start_total_minutes:
                times.append(f"{hour}:{minute:02d}")
    
    for time_str in times:
        hour, minute = map(int, time_str.split(':'))
        callback_data = f"end_{day_index}_{start_hour}_{start_minute}_{hour}_{minute}"
        row.append(InlineKeyboardButton(time_str, callback_data=callback_data))
        
        if len(row) == 3:  # 3 кнопки в ряд
            keyboard.append(row)
            row = []
    
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)
    
    # Кнопка отмены
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{day_index}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    start_time_str = f"{start_hour}:{start_minute:02d}"
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ {date} - {day_name}\nНачало: {start_time_str}\n\nВыберите время окончания смены:",
        reply_markup=reply_markup
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    # Разрешаем только в приватных чатах, кроме /setchat
    if not is_private_chat(update):
        if update.message and update.message.text == '/setchat':
            return await set_chat(update, context)
        return
    
    user = update.effective_user
    user_id = user.id
    
    # Проверяем, зарегистрирован ли пользователь
    existing_user = db.get_user(user_id)
    
    if existing_user:
        # Пользователь уже зарегистрирован
        target_week_dates = get_target_week_dates()
        
        welcome_text = (
            f"👋 С возвращением, {user.first_name}!\n\n"
            f"Ваш ПВЗ: {existing_user[6]}\n"
            f"Текущий период для заполнения: {target_week_dates[0]} - {target_week_dates[-1]}\n\n"
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
        # Новый пользователь - просим ввести пароль
        user_states[user_id] = {'state': 'waiting_password'}
        await update.message.reply_text(
            "👋 Добро пожаловать!\n\n"
            "Для регистрации введите пароль вашего ПВЗ.\n"
            "Пароль можно получить у администратора.",
            reply_markup=get_main_keyboard(user_id)
        )

# ... (остальные функции handle_password, handle_full_name, handle_text_message остаются без изменений)

async def send_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка формы по команде /form"""
    # Разрешаем только в приватных чатах
    if not is_private_chat(update):
        return
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    # Удаляем старое расписание пользователя для целевой недели при перезаполнении
    target_week_dates = get_target_week_dates()
    conn = db.get_connection()
    cursor = conn.cursor()
    placeholders = ','.join('?' for _ in target_week_dates)
    cursor.execute(f'DELETE FROM schedule WHERE user_id = ? AND date IN ({placeholders})', (user_id, *target_week_dates))
    conn.commit()
    conn.close()
    
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
        
        target_week_dates = get_target_week_dates()
        day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        selected_date = target_week_dates[day_index]
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
        start_hour = int(parts[2])
        start_minute = int(parts[3]) if len(parts) > 3 else 0
        
        await query.edit_message_text(
            text=f"⏰ Выбрано начало: {start_hour}:{start_minute:02d}"
        )
        await show_end_time_selection(user_id, day_index, start_hour, start_minute, context)
    
    elif data.startswith("end_"):
        # Пользователь выбрал время окончания
        parts = data.split("_")
        day_index = int(parts[1])
        start_hour = int(parts[2])
        start_minute = int(parts[3])
        end_hour = int(parts[4])
        end_minute = int(parts[5])
        
        target_week_dates = get_target_week_dates()
        day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        selected_date = target_week_dates[day_index]
        day_name = day_names[day_index]
        
        # Сохраняем выбранное время
        time_slot = f"{start_hour}:{start_minute:02d}-{end_hour}:{end_minute:02d}"
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
    target_week_dates = get_target_week_dates()
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    all_pvz = db.get_all_pvz()
    
    for pvz in all_pvz:
        pvz_id, pvz_name, password, chat_id = pvz
        
        report = f"📊 ОТЧЕТ ПО РАСПИСАНИЮ\nПВЗ: {pvz_name}\nПериод: {target_week_dates[0]} - {target_week_dates[-1]}\n\n"
        
        schedule_data = db.get_pvz_schedule_report(pvz_id, target_week_dates)
        
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
        
        for i, date in enumerate(target_week_dates):
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
    # Разрешаем только в приватных чатах
    if not is_private_chat(update):
        return
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    
    if not user:
        await update.message.reply_text(
            "❌ Сначала зарегистрируйтесь с помощью /start",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    target_week_dates = get_target_week_dates()
    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    
    schedule = db.get_user_schedule(user_id, target_week_dates)
    
    # user структура: [0]id, [1]user_id, [2]username, [3]first_name, [4]pvz_id, [5]full_name, [6]pvz_name
    pvz_name = user[6]
    text = f"📋 Ваше расписание на неделю:\nПВЗ: {pvz_name}\nПериод: {target_week_dates[0]} - {target_week_dates[-1]}\n\n"
    
    has_data = False
    for i, date in enumerate(target_week_dates):
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

# ... (остальные функции set_chat, help_command, manual_report, manual_collect, manual_sunday_reminders, stats остаются без изменений)

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
    application.add_handler(CommandHandler("sunday", manual_sunday_reminders))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(handle_button_click))
    
    # Обработчики текстовых сообщений в правильном порядке
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Настраиваем планировщик задач с учетом часового пояса Барнаула
    job_queue = application.job_queue
    
    if job_queue:
        # Задача на субботу (каждую субботу в 9:00 по Барнаулу)
        job_queue.run_daily(
            start_schedule_collection,
            time=datetime.strptime("02:00", "%H:%M").time(),  # 9:00 Барнаул - 7 часов = 02:00 UTC
            days=(5,)
        )
        
        # Задача на воскресенье (каждое воскресенье в 9:00 по Барнаулу)
        job_queue.run_daily(
            send_sunday_reminders,
            time=datetime.strptime("02:00", "%H:%M").time(),  # 9:00 Барнаул - 7 часов = 02:00 UTC
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
