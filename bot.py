import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from database import Database

load_dotenv()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.getenv('BOT_TOKEN')
SUPER_ADMIN_ID = int(os.getenv('SUPER_ADMIN_ID', '457081438'))  # Можно задать в .env

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

db = Database()
user_states = {}
BARNAUL_TZ = timedelta(hours=7)

# Состояния
(CHOOSING_ROLE, EMPLOYEE_PASS, EMPLOYEE_NAME,
 ADMIN_MENU, CREATE_PVZ_NAME, CREATE_PVZ_PASS, CHANGE_PASS,
 PVZ_MENU, PVZ_SETTINGS) = range(9)

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

# Клавиатуры
def start_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Я сотрудник", callback_data="role_employee")],
        [InlineKeyboardButton("Я администратор ПВЗ", callback_data="role_admin")]
    ])

def employee_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("Заполнить анкету")], [KeyboardButton("Моё расписание")]], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Мои ПВЗ"), KeyboardButton("Создать новый ПВЗ")],
        [KeyboardButton("Получить отчёт"), KeyboardButton("Статистика")],
        [KeyboardButton("Отправить напоминания вручную")]
    ], resize_keyboard=True)

# === Основные функции ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это бот для расписания ПВЗ\n\nВыберите вашу роль:",
        reply_markup=start_keyboard()
    )
    return CHOOSING_ROLE

async def role_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "role_employee":
        user_states[user_id] = {"role": "employee"}
        await query.edit_message_text("Введите пароль вашего ПВЗ:")
        return EMPLOYEE_PASS
    else:
        user_states[user_id] = {"role": "admin"}
        await query.edit_message_text("Админ-панель", reply_markup=admin_keyboard())
        return ADMIN_MENU

# === Сотрудник ===
async def employee_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    pvz = db.get_pvz_by_password(password)
    if not pvz:
        await update.message.reply_text("Неверный пароль. Попробуйте снова:")
        return EMPLOYEE_PASS
    user_states[update.effective_user.id]["pvz_id"] = pvz[0]
    await update.message.reply_text("Пароль принят!\nВведите ваше Имя и Фамилию:")
    return EMPLOYEE_NAME

async def employee_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_name = update.message.text.strip()
    if len(full_name.split()) < 2:
        await update.message.reply_text("Введите имя и фамилию:")
        return EMPLOYEE_NAME

    user = update.effective_user
    pvz_id = user_states[user.id]["pvz_id"]
    db.add_user(user.id, user.username, user.first_name, pvz_id, full_name)
    pvz = db.get_pvz_by_id(pvz_id)

    await update.message.reply_text(
        f"Регистрация завершена!\n\nПВЗ: {pvz[1]}\nИмя: {full_name}",
        reply_markup=employee_keyboard()
    )
    return ConversationHandler.END

# === Админ ===
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "Мои ПВЗ":
        await show_my_pvz(update, context)
    elif text == "Создать новый ПВЗ":
        await update.message.reply_text("Введите название нового ПВЗ:")
        return CREATE_PVZ_NAME
    elif text == "Получить отчёт":
        await send_admin_report(context)
        await update.message.reply_text("Отчёт отправлен!")
    elif text == "Статистика":
        await stats(update, context)
    elif text == "Отправить напоминания вручную":
        await start_schedule_collection(context)
        await update.message.reply_text("Напоминания отправлены!")

async def show_my_pvz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pvzs = db.get_pvz_by_admin(update.effective_user.id)
    if not pvzs:
        await update.message.reply_text("У вас нет ПВЗ.")
        return

    keyboard = []
    for pvz in pvzs:
        status = "Беседа привязана" if pvz[3] else "Без беседы"
        keyboard.append([InlineKeyboardButton(f"{pvz[1]} — {status}", callback_data=f"pvz_select_{pvz[0]}")])

    await update.message.reply_text("Ваши ПВЗ:", reply_markup=InlineKeyboardMarkup(keyboard))

# === Напоминания (суббота и воскресенье) ===
async def start_schedule_collection(context: ContextTypes.DEFAULT_TYPE):
    week = get_target_week_dates()
    week_str = f"{week[0]}–{week[-1]}"
    for pvz in db.get_all_pvz():
        if pvz[3]:  # chat_id
            await context.bot.send_message(
                chat_id=pvz[3],
                text=f"Субботнее напоминание!\n\nЗаполните расписание на неделю {week_str}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Заполнить", url=f"https://t.me/{context.bot.username}?start=form")]])
            )

async def send_sunday_reminders(context: ContextTypes.DEFAULT_TYPE):
    week = get_target_week_dates()
    week_str = f"{week[0]}–{week[-1]}"
    for pvz in db.get_all_pvz():
        if not pvz[3]: continue
        # (логика как в старой версии — кто не заполнил)
        # ... упрощённо
        await context.bot.send_message(pvz[3], f"Воскресенье! Проверьте, все ли заполнили {week_str}")

# === Заполнение анкеты, отчёты и т.д. — остаются как в твоём старом коде ===
# (send_day_form, handle_button_click, my_schedule и т.д.)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_ROLE: [CallbackQueryHandler(role_selected, pattern="^role_")],
            EMPLOYEE_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_password)],
            EMPLOYEE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, employee_name)],
            CREATE_PVZ_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: (c.user_data := {"name": u.message.text}, u.message.reply_text("Придумайте пароль:"), CREATE_PVZ_PASS)[1])],
            # ... другие состояния
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_menu)],
        },
        fallbacks=[],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("form", send_form))
    app.add_handler(CommandHandler("myschedule", my_schedule))
    app.add_handler(CallbackQueryHandler(handle_button_click))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(start_schedule_collection, time=datetime.strptime("02:00", "%H:%M").time(), days=(5,))
        job_queue.run_daily(send_sunday_reminders, time=datetime.strptime("02:00", "%H:%M").time(), days=(6,))

    app.run_polling()

if __name__ == "__main__":
    main()