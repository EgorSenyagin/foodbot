#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
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
    DEADLINE_TIME = time(8, 0)

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
            for r in ws.iter_rows(min_row=2, values_only=True):
                if str(r[0]) == student_id:
                    return True, StudentInfo(str(r[0]), r[1], r[2])
        except Exception as e:
            logger.error(e)
        return False, None

    def empty_meals(self):
        return {"breakfast": False, "lunch": False, "snack": False}

    def _find_col(self, ws, date_str):
        h = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        for c in range(2, ws.max_column + 1):
            if ws.cell(1, c).value == h:
                return c
        return None

    def _get_col(self, ws, date_str):
        c = self._find_col(ws, date_str)
        if c:
            return c
        c = ws.max_column + 1
        ws.cell(1, c, datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y"))
        return c

    def _get_row(self, ws, name):
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 1).value == name:
                return r
        r = ws.max_row + 1
        ws.cell(r, 1, name)
        return r

    def save_order(self, student_id, date_str, meals):
        ok, student = self.verify_student(student_id)
        if not ok:
            return
        wb = load_workbook(self.orders_path)
        ws = wb.active
        r = self._get_row(ws, student.full_name)
        c = self._get_col(ws, date_str)
        val = []
        if meals["breakfast"]: val.append("З")
        if meals["lunch"]: val.append("О")
        if meals["snack"]: val.append("П")
        ws.cell(r, c, "+".join(val))
        wb.save(self.orders_path)

    def get_student_orders(self, student_id, date_str):
        ok, student = self.verify_student(student_id)
        if not ok:
            return self.empty_meals()
        wb = load_workbook(self.orders_path, data_only=True)
        ws = wb.active
        c = self._find_col(ws, date_str)
        if not c:
            return self.empty_meals()
        for r in range(2, ws.max_row + 1):
            if ws.cell(r, 1).value == student.full_name:
                v = ws.cell(r, c).value or ""
                return {"breakfast": "З" in v, "lunch": "О" in v, "snack": "П" in v}
        return self.empty_meals()

    def count_for_date(self, date_str):
        wb = load_workbook(self.orders_path, data_only=True)
        ws = wb.active
        c = self._find_col(ws, date_str)
        res = {"breakfast": 0, "lunch": 0, "snack": 0}
        if not c:
            return res
        for r in range(2, ws.max_row + 1):
            v = ws.cell(r, c).value or ""
            if "З" in v: res["breakfast"] += 1
            if "О" in v: res["lunch"] += 1
            if "П" in v: res["snack"] += 1
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
        def b(t, k):
            return InlineKeyboardButton(
                f"{'✅ ' if m[k] else ''}{t}",
                callback_data=f"meal|{date}|{k}"
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

    @staticmethod
    def stats(is_admin: bool):
        kb = []
        if is_admin:
            kb.append([InlineKeyboardButton("⬇ Скачать orders.xlsx", callback_data="download_orders")])
        kb.append([InlineKeyboardButton("⬅ Назад", callback_data="back_main")])
        return InlineKeyboardMarkup(kb)

# ================== БОТ ==================
class FoodBot:
    INPUT_ID, DATES, MEALS = range(3)

    def __init__(self):
        self.db = Database()
        self.sessions = {}

    def session(self, uid):
        return self.sessions.setdefault(uid, {})

    def locked(self, date_str):
        today = datetime.now().date()
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return d < today or (d == today and datetime.now().time() >= Config.DEADLINE_TIME)

    # --------- временные сообщения ---------
    async def send_temp_message(self, chat_id, text, context, delay=3):
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except:
            pass

    # --------- команды ---------
    async def start(self, u: Update, c):
        await u.message.reply_text("🏫 Система питания", reply_markup=KB.main())

    async def button(self, u: Update, c):
        q = u.callback_query
        await q.answer()
        uid = q.from_user.id
        data = q.data.split("|")
        s = self.session(uid)

        if data[0] == "input_id":
            await q.message.edit_text("Введите ID ученика")
            return self.INPUT_ID

        if data[0] == "stats":
            if uid not in Config.ADMIN_IDS:
                await q.message.edit_text("❌ Нет доступа", reply_markup=KB.main())
                return
            today = datetime.now().strftime("%Y-%m-%d")
            nxt = datetime.now() + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            nxt = nxt.strftime("%Y-%m-%d")
            t = self.db.count_for_date(today)
            n = self.db.count_for_date(nxt)
            text = (
                "📊 Статистика\n\n"
                f"Сегодня:\n🍳 {t['breakfast']}  🍲 {t['lunch']}  🥪 {t['snack']}\n\n"
                f"Следующий день:\n🍳 {n['breakfast']}  🍲 {n['lunch']}  🥪 {n['snack']}"
            )
            await q.message.edit_text(text, reply_markup=KB.stats(True))
            return

        if data[0] == "download_orders":
            if uid not in Config.ADMIN_IDS:
                return
            await q.message.reply_document(open(self.db.orders_path, "rb"), filename="orders.xlsx")
            return

        if data[0] == "back_main":
            await q.message.edit_text("Главное меню", reply_markup=KB.main())
            return

        if data[0] == "back_dates":
            await q.message.edit_text("Выберите дату", reply_markup=KB.dates())
            return

        if data[0] == "date":
            s["date"] = data[1]
            m = self.db.get_student_orders(s["id"], data[1])
            await q.message.edit_text("Выберите питание", reply_markup=KB.meals(data[1], m))
            return

        # ====== Выбор еды ======
        if data[0] == "meal":
            if self.locked(s["date"]):
                await self.send_temp_message(q.message.chat_id, "⛔ Редактирование запрещено", c)
                return
            _, d, k = data
            m = self.db.get_student_orders(s["id"], d)
            m[k] = not m[k]
            self.db.save_order(s["id"], d, m)
            await q.message.edit_reply_markup(KB.meals(d, m))
            await self.send_temp_message(q.message.chat_id, "✅ Заказ успешно сохранён", c)

        # ====== Всё на день ======
        if data[0] == "all_day":
            if self.locked(data[1]):
                await self.send_temp_message(q.message.chat_id, "⛔ Редактирование запрещено", c)
                return
            self.db.save_order(
                s["id"],
                data[1],
                {"breakfast": True, "lunch": True, "snack": True}
            )
            await self.send_temp_message(q.message.chat_id, "✅ Питание на день успешно заказано", c)

        # ====== Всё на неделю ======
        if data[0] == "all_week":
            d = datetime.strptime(data[1], "%Y-%m-%d")
            mon = d - timedelta(days=d.weekday())
            for i in range(5):
                day = (mon + timedelta(days=i)).strftime("%Y-%m-%d")
                if not self.locked(day):
                    self.db.save_order(
                        s["id"],
                        day,
                        {"breakfast": True, "lunch": True, "snack": True}
                    )
            await self.send_temp_message(q.message.chat_id, "✅ Питание на неделю успешно заказано", c)

        # ====== Отмена недели ======
        if data[0] == "cancel_week":
            d = datetime.strptime(data[1], "%Y-%m-%d")
            mon = d - timedelta(days=d.weekday())
            for i in range(5):
                day = (mon + timedelta(days=i)).strftime("%Y-%m-%d")
                if not self.locked(day):
                    self.db.save_order(
                        s["id"],
                        day,
                        {"breakfast": False, "lunch": False, "snack": False}
                    )
            await self.send_temp_message(q.message.chat_id, "❌ Питание на неделю отменено", c)

    async def input_id(self, u: Update, c):
        sid = u.message.text.strip()
        ok, st = self.db.verify_student(sid)
        if not ok:
            await u.message.reply_text("❌ Ученик не найден")
            return self.INPUT_ID
        self.session(u.effective_user.id)["id"] = sid
        await u.message.reply_text(f"👤 {st.full_name}", reply_markup=KB.dates())
        return self.DATES

# ================== ЗАПУСК ==================
def main():
    app = Application.builder().token(Config.BOT_TOKEN).build()
    bot = FoodBot()
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
