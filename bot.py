import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

from database import Database

# === Настройка ===
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

db = Database()
user_states = {}
BARNAUL_TZ = timedelta(hours=7)

# === Состояния ===
(
    CHOOSING_ROLE, EMPLOYEE_PASSWORD, EMPLOYEE_NAME,
    ADMIN_MAIN, ADMIN_CREATE_PVZ_NAME, ADMIN_CREATE_PVZ_PASS,
    ADMIN_PVZ_SELECTED, ADMIN_CHANGE_PASSWORD, ADMIN_BIND_CHAT
) = range(9)

# === Время и даты ===
def get_barnaul_time():
    return datetime.utcnow() + BARNAUL_TZ

def get_next_monday():
    today = get_barnaul_time().date()
    days_ahead = 7 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)

def get_target_week_dates():
    monday = get_next_monday()
    return [(monday + timedelta(days=i)).strftime("%d.%m") for i in range(7)]

# === Клавиатуры ===
def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Я сотрудник", callback_data="role_employee")],
        [InlineKeyboardButton("Я администратор ПВЗ", callback_data="role_admin")]
    ])

def employee_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Заполнить анкету")],
        [KeyboardButton("Моё расписание")]
    ], resize_keyboard=True)

def admin_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Мои ПВЗ"), KeyboardButton("Создать новый ПВЗ")],
        [KeyboardButton("Получить отчёт"), KeyboardButton("Статистика")]
    ], resize_keyboard=True)

def pvz_admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Сменить пароль ПВЗ")],
        [KeyboardButton("Привязать беседу для напоминаний")],
        [KeyboardButton("Назад к списку ПВЗ")]
    ], resize_keyboard=True)

# === Старт ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот для расписания ПВЗ\n\nКто вы?",
        reply_markup=start_keyboard()
    )
    return CHOOSING_ROLE

# === Выбор роли (ИСПРАВЛЕНО!) ===
async def role_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "role_employee":
        user_states[user_id] = {"role": "employee"}
        await query.message.reply_text(
            "Вы выбрали: Сотрудник\n\nВведите пароль вашего ПВЗ:",
            reply_markup=ReplyKeyboardRemove()
        )
        await query.message.delete()
        return EMPLOYEE_PASSWORD

    elif query.data == "role_admin":
        user_states[user_id] = {"role": "admin"}
        await query.message.reply_text(
            "Добро пожаловать, администратор!\n\nВыберите действие:",
            reply_markup=admin_main_keyboard()
        )
        await query.message.delete()
        return ADMIN_MAIN

# === Регистрация сотрудника ===
async def employee_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    pvz = db.get_pvz_by_password(password)
    if not pvz:
        await update.message.reply_text("Неверный пароль. Попробуйте снова:")
        return EMPLOYEE_PASSWORD

    user_states[update.effective_user.id]["pvz_id"] = pvz[0]
    await update.message.reply_text("Пароль принят!\nВведите ваше Имя и Фамилию:")
    return EMPLOYEE_NAME

async def employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    if len(full_name.split()) < 2:
        await update.message.reply_text("Введите и имя, и фамилию:")
        return EMPLOYEE_NAME

    user = update.effective_user
    pvz_id = user_states[user.id]["pvz_id"]
    db.add_user(user.id, user.username, user.first_name, pvz_id, full_name)
    pvz = db.get_pvz_by_id(pvz_id)

    await update.message.reply_text(
        f"Регистрация завершена!\n\nПВЗ: {pvz[1]}\nИмя: {full_name}\n\nТеперь вы можете заполнять анкету.",
        reply_markup=employee_keyboard()
    )
    return ConversationHandler.END

# === Админ: главное меню ===
async def admin_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "Мои ПВЗ":
        await show_my_pvz(update, context)
    elif text == "Создать новый ПВЗ":
        await update.message.reply_text("Введите название нового ПВЗ:")
        return ADMIN_CREATE_PVZ_NAME
    elif text == "Получить отчёт":
        await update.message.reply_text("Отчёт в разработке...")
    elif text == "Статистика":
        await show_stats(update, context)

# === Мои ПВЗ ===
async def show_my_pvz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pvzs = db.get_pvz_by_admin(update.effective_user.id) or []
    if not pvzs:
        await update.message.reply_text("У вас нет ПВЗ. Создайте первый!")
        return

    keyboard = []
    for pvz in pvzs:
        status = "беседа привязана" if pvz[3] else "без беседы"
        keyboard.append([InlineKeyboardButton(f"{pvz[1]} — {status}", callback_data=f"pvz_{pvz[0]}")])

    await update.message.reply_text("Ваши ПВЗ:", reply_markup=InlineKeyboardMarkup(keyboard))

# === Управление ПВЗ ===
async def pvz_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pvz_id = int(query.data.split("_")[1])
    context.user_data["current_pvz_id"] = pvz_id
    pvz = db.get_pvz_by_id(pvz_id)

    await query.message.reply_text(
        f"Управление ПВЗ: {pvz[1]}\n\nВыберите действие:",
        reply_markup=pvz_admin_keyboard()
    )
    await query.message.delete()
    return ADMIN_PVZ_SELECTED

async def admin_pvz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    pvz_id = context.user_data.get("current_pvz_id")

    if text == "Сменить пароль ПВЗ":
        await update.message.reply_text("Введите новый пароль:")
        return ADMIN_CHANGE_PASSWORD
    elif text == "Привязать беседу для напоминаний":
        await update.message.reply_text("Перешлите сюда любое сообщение из нужной беседы.")
        return ADMIN_BIND_CHAT
    elif text == "Назад к списку ПВЗ":
        await show_my_pvz(update, context)
        return ADMIN_MAIN

# === Создание ПВЗ ===
async def admin_create_pvz_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_pvz_name"] = update.message.text.strip()
    await update.message.reply_text("Придумайте пароль для сотрудников:")
    return ADMIN_CREATE_PVZ_PASS

async def admin_create_pvz_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data["new_pvz_name"]
    password = update.message.text.strip()
    admin_id = update.effective_user.id

    pvz_id = db.create_pvz(name, password, admin_id)

    await update.message.reply_text(
        f"ПВЗ «{name}» создан!\n"
        f"Пароль: {password}\n"
        f"Вы — администратор этого ПВЗ."
    )
    return ADMIN_MAIN

# === Смена пароля ===
async def admin_change_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pass = update.message.text.strip()
    pvz_id = context.user_data["current_pvz_id"]
    db.update_pvz_password(pvz_id, new_pass)
    pvz = db.get_pvz_by_id(pvz_id)
    await update.message.reply_text(f"Пароль ПВЗ «{pvz[1]}» изменён на: {new_pass}")
    return ADMIN_PVZ_SELECTED

# === Привязка беседы ===
async def admin_bind_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.forward_from_chat:
        await update.message.reply_text("Пожалуйста, перешлите сообщение из нужной беседы!")
        return ADMIN_BIND_CHAT

    chat = update.message.forward_from_chat
    pvz_id = context.user_data["current_pvz_id"]
    db.set_pvz_chat_id(pvz_id, str(chat.id))

    await update.message.reply_text(
        f"Беседа «{chat.title or 'Без названия'}» привязана!\n"
        f"Теперь напоминания будут сюда."
    )
    return ADMIN_PVZ_SELECTED

# === Статистика ===
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pvzs = db.get_pvz_by_admin(update.effective_user.id) or []
    if not pvzs:
        await update.message.reply_text("У вас нет ПВЗ.")
        return

    text = "Статистика:\n\n"
    for pvz in pvzs:
        users = db.get_users_by_pvz(pvz[0])
        chat_status = "привязана" if pvz[3] else "не привязана"
        text += f"• {pvz[1]}\n  Сотрудников: {len(users)}\n  Беседа: {chat_status}\n\n"

    await update.message.reply_text(text)

# === Напоминания ===
async def start_schedule_collection(context: ContextTypes.DEFAULT_TYPE):
    week = get_target_week_dates()
    week_str = f"{week[0]} – {week[-1]}"
    for pvz in db.get_all_pvz():
        if pvz[3]:
            try:
                await context.bot.send_message(
                    chat_id=pvz[3],
                    text=f"Субботнее напоминание!\n\n"
                         f"Заполните расписание на неделю {week_str}\n\n",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Заполнить анкету", url=f"https://t.me/{context.bot.username}?start=form")
                    ]])
                )
            except Exception as e:
                logging.error(f"Ошибка отправки в {pvz[1]}: {e}")

async def send_sunday_reminders(context: ContextTypes.DEFAULT_TYPE):
    week = get_target_week_dates()
    week_str = f"{week[0]} – {week[-1]}"
    for pvz in db.get_all_pvz():
        if pvz[3]:
            await context.bot.send_message(pvz[3], f"Воскресенье! Заполните расписание на {week_str}")

# === Основной запуск ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ROLE: [CallbackQueryHandler(role_selected, pattern="^role_")],
            EMPLOYEE_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_password)],
            EMPLOYEE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_name)],
            ADMIN_MAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_main)],
            ADMIN_CREATE_PVZ_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_pvz_name)],
            ADMIN_CREATE_PVZ_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_create_pvz_pass)],
            ADMIN_PVZ_SELECTED: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pvz_menu)],
            ADMIN_CHANGE_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_password)],
            ADMIN_BIND_CHAT: [MessageHandler(filters.FORWARDED, admin_bind_chat)],
        },
        fallbacks=[],
        per_user=True
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(pvz_selected, pattern="^pvz_\\d+$"))

    # Напоминания
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(start_schedule_collection, time=datetime.strptime("02:00", "%H:%M").time(), days=(5,))
        job_queue.run_daily(send_sunday_reminders, time=datetime.strptime("02:00", "%H:%M").time(), days=(6,))

    logging.info("Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()