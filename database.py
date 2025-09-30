import sqlite3
import logging
from datetime import datetime


class Database:
    def __init__(self, db_name='schedule_bot.db'):
        self.db_name = db_name
        self.init_database()

    def init_database(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        # Таблица ПВЗ
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pvz (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                chat_id TEXT
            )
        ''')

        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                pvz_id INTEGER,
                full_name TEXT,
                FOREIGN KEY (pvz_id) REFERENCES pvz (id)
            )
        ''')

        # Таблица расписания
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT NOT NULL,
                time_slot TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # Добавляем ПВЗ Промышленная_6
        cursor.execute('''
            INSERT OR IGNORE INTO pvz (name, password) VALUES 
            ('Промышленная_6', '1525')
        ''')

        conn.commit()
        conn.close()
        logging.info("База данных инициализирована")

    def get_connection(self):
        """Получить соединение с базой данных"""
        return sqlite3.connect(self.db_name)

    def get_pvz_by_password(self, password):
        """Получить ПВЗ по паролю"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pvz WHERE password = ?', (password,))
        pvz = cursor.fetchone()
        conn.close()
        return pvz

    def get_pvz_by_id(self, pvz_id):
        """Получить ПВЗ по ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pvz WHERE id = ?', (pvz_id,))
        pvz = cursor.fetchone()
        conn.close()
        return pvz

    def add_user(self, user_id, username, first_name, pvz_id, full_name=None):
        """Добавить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, pvz_id, full_name)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, pvz_id, full_name))
        conn.commit()
        conn.close()

    def get_user(self, user_id):
        """Получить пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.*, p.name as pvz_name 
            FROM users u 
            LEFT JOIN pvz p ON u.pvz_id = p.id 
            WHERE u.user_id = ?
        ''', (user_id,))
        user = cursor.fetchone()
        conn.close()
        return user

    def save_schedule(self, user_id, date, time_slot):
        """Сохранить расписание"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Удаляем старую запись для этой даты
        cursor.execute('DELETE FROM schedule WHERE user_id = ? AND date = ?', (user_id, date))

        # Добавляем новую запись
        cursor.execute('''
            INSERT INTO schedule (user_id, date, time_slot)
            VALUES (?, ?, ?)
        ''', (user_id, date, time_slot))

        conn.commit()
        conn.close()

    def delete_user_schedule(self, user_id):
        """Удалить все расписание пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM schedule WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

    def get_user_schedule(self, user_id, week_dates=None):
        """Получить расписание пользователя"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if week_dates:
            placeholders = ','.join('?' for _ in week_dates)
            cursor.execute(f'''
                SELECT date, time_slot FROM schedule 
                WHERE user_id = ? AND date IN ({placeholders})
            ''', (user_id, *week_dates))
        else:
            cursor.execute('SELECT date, time_slot FROM schedule WHERE user_id = ?', (user_id,))

        schedule = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in schedule}

    def get_pvz_schedule_report(self, pvz_id, week_dates):
        """Получить отчет по расписанию для ПВЗ"""
        conn = self.get_connection()
        cursor = conn.cursor()

        placeholders = ','.join('?' for _ in week_dates)
        cursor.execute(f'''
            SELECT u.first_name, u.username, u.user_id, s.date, s.time_slot, u.full_name
            FROM schedule s
            JOIN users u ON s.user_id = u.user_id
            WHERE u.pvz_id = ? AND s.date IN ({placeholders})
            ORDER BY s.date, u.full_name
        ''', (pvz_id, *week_dates))

        schedule_data = cursor.fetchall()
        conn.close()
        return schedule_data

    def get_all_pvz(self):
        """Получить все ПВЗ"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pvz')
        pvz_list = cursor.fetchall()
        conn.close()
        return pvz_list

    def set_pvz_chat_id(self, pvz_id, chat_id):
        """Установить chat_id для ПВЗ (для напоминаний в беседу)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE pvz SET chat_id = ? WHERE id = ?', (chat_id, pvz_id))
        conn.commit()
        conn.close()

    def get_pvz_chat_id(self, pvz_id):
        """Получить chat_id ПВЗ"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT chat_id FROM pvz WHERE id = ?', (pvz_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None