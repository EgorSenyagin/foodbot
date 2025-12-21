#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
import logging
from datetime import datetime, timedelta, time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

from openpyxl import Workbook, load_workbook
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters
)

# ================== НАСТРОЙКИ ==================
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_IDS = [6056091640]
    DATA_DIR = "data"
    ORDERS_FILE = "orders.xlsx"
    STUDENTS_FILE = "students.xlsx"
    DEADLINE_TIME = time(8, 0)  # 08:00


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# ================== МОДЕЛИ ==================
@dataclass
class StudentInfo:
    student_id: str
    full_name: str
    class_name: str


# ================== БАЗА ДАННЫХ ==================
class Database:

    def __init__(self):
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        self.orders_path = os.path.join(Config.DATA_DIR, Config.ORDERS_FILE)
        self.students_path = os.path.join(Config.DATA_DIR, Config.STUDENTS_FILE)
        self._init_orders_file()

    def _init_orders_file(self):
        if os.path.exists(self.orders_path):
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Заказы"

        headers = ["Ученик"]
        d = datetime.now()
        added = 0

        while added < 100:
            if d.weekday() < 5:
                headers.append(d.strftime("%d.%m.%Y"))
                added += 1
            d += timedelta(days=1)

        ws.append(headers)
        wb.save(self.orders_path)

    def verify_student(self, student_id: str) -> Tuple[bool, Optional[StudentInfo]]:
        try:
            wb = load_workbook(self.students_path, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(min_row=2, values_only=True):
                if str(row[0]) == student_id:
                    return True, StudentInfo(str(row[0]), row[1], row[2])
        except Exception as e:
            logger.error(e)
        return False, None

    def empty_meals(self):
        return {"breakfast": False, "lunch": False, "snack": False}

    def _find_column(self, ws, date_str: str):
        header = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        for c in range(2, ws.max_column + 1):
            if ws.cell(1, c).value == header:
                return c
        return None

    def _get_or_create_column(self, ws, date_str: str):
        col = self._find_column(ws, date_str)
        if col:
            return col
        col = ws.max_column + 1
        ws.cell(1, col, datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y"))
        return col

    def _get_or_create_row(self, ws, student_name: str):
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 1).value == student_name:
                return r
        r = ws.max_row + 1
        ws.cell(r, 1, student_name)
        return r

    def save_order(self, student_id: str, date_str: str, meals: Dict[str, bool]):
        ok, student = self.verify_student(student_id)
        if not ok:
            return

        wb = load_workbook(self.orders_path)
        ws = wb.active

        row = self._get_or_create_row(ws, student.full_name)
        col = self._get_or_create_column(ws, date_str)

        symbols = []
        if meals["breakfast"]: symbols.append("З")
        if meals["lunch"]: symbols.append("О")
        if meals["snack"]: symbols.append("П")

        ws.cell(row, col, "+".join(symbols))
        wb.save(self.orders_path)

    def get_student_orders(self, student_id: str, date_str: str):
        ok, student = self.verify_student(student_id)
        if not ok:
            return self.empty_meals()

        wb = load_workbook(self.orders_path, data_only=True)
        ws = wb.active

        col = self._find_column(ws, date_str)
        if not col:
            return self.empty_meals()

        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 1).value == student.full_name:
                val = ws.cell(r, col).value or ""
                return {
                    "breakfast": "З" in val,
                    "lunch": "О" in val,
                    "snack": "П" in val
                }
        return self.empty_meals()

    def count_for_date(self, date_str: str):
        wb = load_workbook(self.orders_path, data_only=True)
        ws = wb.active
        col = self._find_column(ws, date_str)
        res = {"breakfast": 0, "lunch": 0, "snack": 0}
        if not col:
            return res

        for r in range(2, ws.max_row + 1):
            val = ws.cell(r, col).value or ""
            if "З" in val: res["breakfast"] += 1
            if "О" in val: res["lunch"] += 1
            if "П" in val: res["snack"] += 1
        return res


# ================== КНОПКИ ==================
class KB:

    @staticmethod
    def main():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Ввести ID", callback_data="input_id")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
        ])

    @staticmethod
    def dates():
        kb = []
        d = datetime.now()
        added = 0
        while added < 10:
            if d.weekday() < 5:
                kb.append([InlineKeyboardButton(
                    f"{d.strftime('%d.%m')} ({DAY_NAMES_RU[d.weekday()]})",
                    callback_data=f"date|{d.strftime('%Y-%m-%d')}"
                )])
                added += 1
            d += timedelta(days=1)
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data="back_main")])
        return InlineKeyboardMarkup(kb)

    @staticmethod
    def meals(date, m):
        def b(text, key):
            return InlineKeyboardButton(
                f"{'✅ ' if m[key] else ''}{text}",
                callback_data=f"meal|{date}|{key}"
            )

        return InlineKeyboardMarkup([
            [b("🍳 Завтрак", "breakfast")],
            [b("🍲 Обед", "lunch")],
            [b("🥪 Полдник", "snack")],
            [InlineKeyboardButton("✅ Всё на день", callback_data=f"all_day|{date}")],
            [InlineKeyboardButton("📅 Всё на неделю", callback_data=f"all_week|{date}")],
            [InlineKeyboardButton("❌ Отменить неделю", callback_data=f"cancel_week|{date}")],
            [InlineKeyboardButton("⬅ К датам", callback_data="back_dates")]
        ])


# ================== БОТ ==================
class FoodBot:

    INPUT_ID, DATES, MEALS = range(3)

    def __init__(self):
        self.db = Database()
        self.sessions = {}

    def session(self, uid):
        return self.sessions.setdefault(uid, {})

    def is_edit_locked(self, date_str: str) -> bool:
        today = datetime.now().date()
        selected = datetime.strptime(date_str, "%Y-%m-%d").date()
        if selected < today:
            return True
        if selected == today and datetime.now().time() >= Config.DEADLINE_TIME:
            return True
        return False

    async def start(self, u: Update, c):
        await u.message.reply_text("🏫 Система питания", reply_markup=KB.main())

    async def button(self, u: Update, c):
        q = u.callback_query
        await q.answer()
        data = q.data.split("|")
        uid = q.from_user.id
        s = self.session(uid)

        if data[0] == "input_id":
            await q.message.edit_text("Введите ID ученика")
            return self.INPUT_ID

        if data[0] == "stats":
            if uid not in Config.ADMIN_IDS:
                await q.message.edit_text("❌ Нет доступа", reply_markup=KB.main())
                return

            today = datetime.now().strftime("%Y-%m-%d")
            next_day = datetime.now() + timedelta(days=1)
            while next_day.weekday() >= 5:
                next_day += timedelta(days=1)
            next_day = next_day.strftime("%Y-%m-%d")

            t = self.db.count_for_date(today)
            n = self.db.count_for_date(next_day)

            text = (
                "📊 Статистика заказов\n\n"
                f"Сегодня:\n"
                f"🍳Завтраки {t['breakfast']}  🍲Обеды {t['lunch']}  🥪Полдники {t['snack']}\n\n"
                f"Следующий день:\n"
                f"🍳Завтраки {n['breakfast']}  🍲Обеды {n['lunch']}  🥪Полдники {n['snack']}"
            )

            await q.message.edit_text(text, reply_markup=KB.main())
            return

        if data[0] == "back_main":
            await q.message.edit_text("Главное меню", reply_markup=KB.main())
            return

        if data[0] == "back_dates":
            await q.message.edit_text("Выберите дату", reply_markup=KB.dates())
            return

        if data[0] == "date":
            s["date"] = data[1]
            m = self.db.get_student_orders(s["id"], s["date"])
            await q.message.edit_text("Выберите питание", reply_markup=KB.meals(s["date"], m))
            return

        if data[0] == "meal":
            if self.is_edit_locked(s["date"]):
                await q.answer("⛔ Редактирование запрещено", show_alert=True)
                return
            _, date, meal = data
            m = self.db.get_student_orders(s["id"], date)
            m[meal] = not m[meal]
            self.db.save_order(s["id"], date, m)
            await q.message.edit_reply_markup(KB.meals(date, m))

        if data[0] in ("all_day", "all_week", "cancel_week"):
            if self.is_edit_locked(data[1]):
                await q.answer("⛔ Редактирование запрещено", show_alert=True)
                return

        if data[0] == "all_day":
            m = {"breakfast": True, "lunch": True, "snack": True}
            self.db.save_order(s["id"], data[1], m)
            await q.message.edit_reply_markup(KB.meals(data[1], m))

        if data[0] == "all_week":
            d = datetime.strptime(data[1], "%Y-%m-%d")
            monday = d - timedelta(days=d.weekday())
            for i in range(5):
                day = (monday + timedelta(days=i)).strftime("%Y-%m-%d")
                if not self.is_edit_locked(day):
                    self.db.save_order(s["id"], day,
                        {"breakfast": True, "lunch": True, "snack": True})
            await q.answer("✅ Заказы на неделю оформлены")

        if data[0] == "cancel_week":
            d = datetime.strptime(data[1], "%Y-%m-%d")
            monday = d - timedelta(days=d.weekday())
            for i in range(5):
                day = (monday + timedelta(days=i)).strftime("%Y-%m-%d")
                if not self.is_edit_locked(day):
                    self.db.save_order(s["id"], day,
                        {"breakfast": False, "lunch": False, "snack": False})
            await q.answer("❌ Заказы на неделю отменены")

    async def input_id(self, u: Update, c):
        sid = u.message.text.strip()
        ok, student = self.db.verify_student(sid)
        if not ok:
            await u.message.reply_text("❌ Ученик не найден")
            return self.INPUT_ID

        self.session(u.effective_user.id)["id"] = sid
        await u.message.reply_text(f"👤 Ученик: {student.full_name}", reply_markup=KB.dates())
        return self.DATES


# ================== ЗАПУСК ==================
def main():
    bot = FoodBot()
    app = Application.builder().token(Config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(bot.button, pattern="input_id")],
        states={
            bot.INPUT_ID: [MessageHandler(filters.TEXT, bot.input_id)],
            bot.DATES: [CallbackQueryHandler(bot.button)],
            bot.MEALS: [CallbackQueryHandler(bot.button)],
        },
        fallbacks=[]
    ))
    app.add_handler(CallbackQueryHandler(bot.button))
    app.run_polling()


if __name__ == "__main__":
    main()

