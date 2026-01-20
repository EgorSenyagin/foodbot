#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import asyncio
import json
import re
from datetime import datetime, timedelta, time
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List, Any
from enum import Enum

from openpyxl import Workbook, load_workbook
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)


# ================== НАСТРОЙКИ ==================
class Config:
    BOT_TOKEN = "8156286210:AAG0WcdjO9vsoLoDVD6O-H0WErClTcjXqEM"
    ADMIN_IDS = [6056091640]
    DATA_DIR = "data"
    TEMPLATE_FILE = "Табличка для бота по питанию.xlsx"
    ORDERS_FILE = "orders.xlsx"
    STUDENTS_FILE = "students.xlsx"
    SESSIONS_FILE = "sessions.json"
    DEADLINE_TIME = time(8, 0)


# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class MealType(Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    SNACK = "snack"


# ================== МОДЕЛИ ==================
@dataclass
class StudentInfo:
    student_id: str
    full_name: str
    class_name: str


# ================== МЕНЕДЖЕР ШАБЛОНА ==================
class TemplateManager:
    def __init__(self, template_path: str):
        self.template_path = template_path
        self.workbook = None
        self.structure = {}

    def load_template(self) -> bool:
        """Загружает и анализирует шаблон"""
        if not os.path.exists(self.template_path):
            logger.error(f"Файл шаблона не найден: {self.template_path}")
            return False

        try:
            logger.info(f"Загрузка шаблона: {self.template_path}")
            self.workbook = load_workbook(self.template_path)
            self.structure = self._analyze_structure()
            logger.info(f"Шаблон загружен успешно. Листов: {len(self.workbook.sheetnames)}")
            return True
        except Exception as e:
            logger.error(f"Ошибка загрузки шаблона: {e}", exc_info=True)
            return False

    def _analyze_structure(self) -> Dict:
        """Анализирует структуру шаблона"""
        structure = {}

        for sheet_name in self.workbook.sheetnames:
            sheet = self.workbook[sheet_name]
            logger.info(f"Анализ листа: {sheet_name}")

            sheet_structure = {
                'class_name': sheet_name,
                'date_columns': {},  # дата -> (завтрак_кол, обед_кол, полдник_кол)
                'students': {},  # ФИО -> строка
                'date_row': None,
                'students_start_row': None
            }

            # Ищем строку с датами
            for row in range(1, 10):
                cell = sheet.cell(row=row, column=3)  # Колонка C
                if cell.value and self._is_date(cell.value):
                    sheet_structure['date_row'] = row
                    logger.info(f"Найдена строка с датами: строка {row}")
                    break

            if not sheet_structure['date_row']:
                # Пробуем другие колонки
                for row in range(1, 10):
                    for col in range(3, 10):
                        cell = sheet.cell(row=row, column=col)
                        if cell.value and self._is_date(cell.value):
                            sheet_structure['date_row'] = row
                            logger.info(f"Найдена строка с датами: строка {row}, колонка {col}")
                            break
                    if sheet_structure['date_row']:
                        break

            if not sheet_structure['date_row']:
                sheet_structure['date_row'] = 3
                logger.warning(f"Не найдена строка с датами для листа {sheet_name}, используем строку 3")

            # Парсим даты
            self._parse_dates(sheet, sheet_structure)

            # Ищем начало списка учеников
            for row in range(1, 20):
                if sheet.cell(row=row, column=1).value == "пп":
                    sheet_structure['students_start_row'] = row + 1
                    logger.info(f"Начало списка учеников: строка {row + 1}")
                    break

            if not sheet_structure['students_start_row']:
                # Ищем по заголовку "ФИО"
                for row in range(1, 20):
                    if sheet.cell(row=row, column=2).value == "ФИО":
                        sheet_structure['students_start_row'] = row + 1
                        logger.info(f"Начало списка учеников по ФИО: строка {row + 1}")
                        break

            if not sheet_structure['students_start_row']:
                sheet_structure['students_start_row'] = 4
                logger.warning(f"Не найдено начало списка учеников для листа {sheet_name}")

            # Парсим учеников
            self._parse_students(sheet, sheet_structure)

            structure[sheet_name] = sheet_structure

        return structure

    def _is_date(self, value) -> bool:
        """Проверяет, является ли значение датой"""
        if isinstance(value, datetime):
            return True

        value_str = str(value)
        date_patterns = [
            r'\d{4}-\d{2}-\d{2}',
            r'\d{2}\.\d{2}\.\d{4}',
            r'\d{2}/\d{2}/\d{4}'
        ]

        for pattern in date_patterns:
            if re.search(pattern, value_str):
                return True

        return False

    def _parse_dates(self, sheet, sheet_structure: Dict):
        """Парсит даты из шаблона"""
        date_row = sheet_structure['date_row']

        col = 3  # Начинаем с колонки C
        while col <= sheet.max_column:
            date_cell = sheet.cell(row=date_row, column=col)
            date_value = self._normalize_date(date_cell.value)

            if date_value:
                # Проверяем, что следующие две колонки - это з/о/п
                next_col1 = sheet.cell(row=date_row + 1, column=col).value
                next_col2 = sheet.cell(row=date_row + 1, column=col + 1).value
                next_col3 = sheet.cell(row=date_row + 1, column=col + 2).value

                # Если в следующих колонках з/о/п или они пустые
                if (next_col1 in ["з", "З", ""] and
                        next_col2 in ["о", "О", ""] and
                        next_col3 in ["п", "П", ""]):

                    sheet_structure['date_columns'][date_value] = {
                        'breakfast_col': col,
                        'lunch_col': col + 1,
                        'snack_col': col + 2
                    }
                    logger.debug(f"Найдена дата {date_value} в колонках {col}-{col + 2}")
                    col += 3  # Переходим к следующей дате
                else:
                    col += 1
            else:
                col += 1

    def _parse_students(self, sheet, sheet_structure: Dict):
        """Парсит список учеников"""
        start_row = sheet_structure['students_start_row']

        for row in range(start_row, sheet.max_row + 1):
            name_cell = sheet.cell(row=row, column=2)  # Колонка B - ФИО
            if name_cell.value:
                student_name = str(name_cell.value).strip()
                if (student_name and
                        student_name != "Итого:" and
                        not student_name.startswith("Всего:") and
                        not student_name.startswith("Итог")):
                    sheet_structure['students'][student_name] = row
                    logger.debug(f"Найден ученик: {student_name} в строке {row}")

    def _normalize_date(self, value) -> Optional[str]:
        """Приводит дату к стандартному формату YYYY-MM-DD"""
        if not value:
            return None

        try:
            if isinstance(value, datetime):
                return value.strftime("%Y-%m-%d")

            value_str = str(value).strip()

            # Убираем время если есть
            if " 00:00:00" in value_str:
                value_str = value_str.replace(" 00:00:00", "")

            # Пробуем разные форматы
            date_formats = [
                "%Y-%m-%d",
                "%d.%m.%Y",
                "%d/%m/%Y",
                "%d-%m-%Y"
            ]

            for fmt in date_formats:
                try:
                    dt = datetime.strptime(value_str, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue

            # Пробуем извлечь дату из строки
            date_patterns = [
                r'\d{4}-\d{2}-\d{2}',
                r'\d{2}\.\d{2}\.\d{4}',
                r'\d{2}/\d{2}/\d{4}'
            ]

            for pattern in date_patterns:
                match = re.search(pattern, value_str)
                if match:
                    date_str = match.group()
                    for fmt in date_formats:
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            continue

        except Exception as e:
            logger.debug(f"Ошибка нормализации даты '{value}': {e}")

        return None

    def update_order(self, student_name: str, date_str: str, meals: Dict[str, bool]) -> bool:
        """Обновляет заказ в шаблоне"""
        if not self.workbook:
            if not self.load_template():
                logger.error("Не удалось загрузить шаблон")
                return False

        try:
            # Находим ученика
            sheet_name, student_row = self.find_student(student_name)
            if not sheet_name or not student_row:
                logger.error(f"Ученик не найден в шаблоне: {student_name}")
                logger.info(f"Доступные ученики: {list(self.get_all_students_names())}")
                return False

            # Находим колонки для даты
            sheet_structure = self.structure.get(sheet_name)
            if not sheet_structure:
                logger.error(f"Структура листа {sheet_name} не найдена")
                return False

            date_info = sheet_structure['date_columns'].get(date_str)
            if not date_info:
                logger.error(f"Дата {date_str} не найдена в листе {sheet_name}")
                logger.info(f"Доступные даты в {sheet_name}: {list(sheet_structure['date_columns'].keys())}")
                return False

            sheet = self.workbook[sheet_name]

            # Обновляем ячейки
            breakfast_col = date_info['breakfast_col']
            lunch_col = date_info['lunch_col']
            snack_col = date_info['snack_col']

            # Сохраняем текущие значения для отладки
            old_breakfast = sheet.cell(row=student_row, column=breakfast_col).value
            old_lunch = sheet.cell(row=student_row, column=lunch_col).value
            old_snack = sheet.cell(row=student_row, column=snack_col).value

            if meals.get('breakfast'):
                sheet.cell(row=student_row, column=breakfast_col, value="З")
            else:
                sheet.cell(row=student_row, column=breakfast_col, value="")

            if meals.get('lunch'):
                sheet.cell(row=student_row, column=lunch_col, value="О")
            else:
                sheet.cell(row=student_row, column=lunch_col, value="")

            if meals.get('snack'):
                sheet.cell(row=student_row, column=snack_col, value="П")
            else:
                sheet.cell(row=student_row, column=snack_col, value="")

            # Сохраняем изменения
            self.workbook.save(self.template_path)

            logger.info(f"Шаблон обновлен: {student_name} - {date_str}")
            logger.debug(f"Старые значения: З={old_breakfast}, О={old_lunch}, П={old_snack}")
            logger.debug(f"Новые значения: З={'З' if meals.get('breakfast') else ''}, "
                         f"О={'О' if meals.get('lunch') else ''}, "
                         f"П={'П' if meals.get('snack') else ''}")

            return True

        except Exception as e:
            logger.error(f"Ошибка обновления шаблона: {e}", exc_info=True)
            return False

    def find_student(self, student_name: str) -> Tuple[Optional[str], Optional[int]]:
        """Находит ученика в шаблоне"""
        for sheet_name, sheet_structure in self.structure.items():
            for name, row in sheet_structure['students'].items():
                # Сравниваем без учета регистра и лишних пробелов
                if name.strip().lower() == student_name.strip().lower():
                    return sheet_name, row
        return None, None

    def get_all_students_names(self) -> List[str]:
        """Получает список всех имен учеников"""
        names = []
        for sheet_structure in self.structure.values():
            names.extend(sheet_structure['students'].keys())
        return names

    def get_all_dates(self) -> List[str]:
        """Получает все даты из шаблона"""
        dates = set()
        for sheet_structure in self.structure.values():
            dates.update(sheet_structure['date_columns'].keys())
        return sorted(list(dates))


# ================== БАЗА ДАННЫХ ==================
class Database:
    def __init__(self):
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        self.template_path = os.path.join(Config.DATA_DIR, Config.TEMPLATE_FILE)
        self.orders_path = os.path.join(Config.DATA_DIR, Config.ORDERS_FILE)
        self.students_path = os.path.join(Config.DATA_DIR, Config.STUDENTS_FILE)
        self.sessions_path = os.path.join(Config.DATA_DIR, Config.SESSIONS_FILE)

        self.template_manager = TemplateManager(self.template_path)

        # Инициализация файлов
        self._init_files()

    def _init_files(self):
        """Инициализация всех файлов"""
        # Проверяем существование students.xlsx
        if not os.path.exists(self.students_path):
            logger.error(f"Файл {self.students_path} не найден!")
            print(f"\n❌ ВНИМАНИЕ: Файл {self.students_path} не найден!")
            print("Создайте файл students.xlsx со следующими колонками:")
            print("1. ID ученика (числовой, например: 100953)")
            print("2. ФИО (например: Данильченко Андрей)")
            print("3. Класс (например: 1А)")
            return

        # Загружаем шаблон
        if os.path.exists(self.template_path):
            if self.template_manager.load_template():
                logger.info("Шаблон загружен успешно")
            else:
                logger.warning("Не удалось загрузить шаблон")
        else:
            logger.warning(f"Файл шаблона не найден: {self.template_path}")

        # Создаем или обновляем orders.xlsx
        self._create_or_update_orders_file()

    def _create_or_update_orders_file(self):
        """Создает или обновляет файл заказов"""
        try:
            # Загружаем учеников из students.xlsx
            student_wb = load_workbook(self.students_path, data_only=True)
            student_ws = student_wb.active

            # Получаем список учеников
            students = []
            for row in student_ws.iter_rows(min_row=2, values_only=True):
                if row and row[0] and row[1]:
                    students.append({
                        'id': str(row[0]),
                        'name': row[1],
                        'class': row[2] if len(row) > 2 else ""
                    })

            # Получаем список дат из шаблона или создаем на 30 дней
            dates = []
            if self.template_manager.workbook:
                dates = self.template_manager.get_all_dates()
                logger.info(f"Загружено {len(dates)} дат из шаблона")
            else:
                # Создаем даты на 30 рабочих дней вперед
                today = datetime.now()
                added = 0
                date = today
                while added < 150:
                    if date.weekday() < 5:  # Только будни
                        dates.append(date.strftime("%Y-%m-%d"))
                        added += 1
                    date += timedelta(days=1)
                logger.info(f"Создано {len(dates)} дат (30 рабочих дней)")

            # Проверяем существует ли orders.xlsx
            if os.path.exists(self.orders_path):
                # Обновляем существующий файл
                self._update_orders_file(students, dates)
            else:
                # Создаем новый файл
                self._create_new_orders_file(students, dates)

        except Exception as e:
            logger.error(f"Ошибка создания/обновления orders.xlsx: {e}")

    def _create_new_orders_file(self, students: List[Dict], dates: List[str]):
        """Создает новый файл заказов"""
        wb = Workbook()
        ws = wb.active
        ws.title = "Заказы"

        # Заголовки
        headers = ["ID", "ФИО", "Класс"]

        # Добавляем колонки для каждой даты и каждого приема пищи
        for date_str in dates:
            headers.extend([
                f"{date_str}_breakfast",
                f"{date_str}_lunch",
                f"{date_str}_snack"
            ])

        ws.append(headers)

        # Добавляем строки для учеников
        for student in students:
            student_row = [student['id'], student['name'], student['class']]
            # Пустые ячейки для заказов
            student_row.extend([""] * (len(dates) * 3))
            ws.append(student_row)

        wb.save(self.orders_path)
        logger.info(f"Создан новый файл orders.xlsx: {len(students)} учеников, {len(dates)} дат")

    def _update_orders_file(self, students: List[Dict], dates: List[str]):
        """Обновляет существующий файл заказов"""
        wb = load_workbook(self.orders_path)
        ws = wb.active

        # Получаем текущие заголовки
        current_headers = []
        for col in range(1, ws.max_column + 1):
            current_headers.append(ws.cell(1, col).value)

        # Добавляем недостающие даты
        new_dates = []
        for date_str in dates:
            date_headers = [
                f"{date_str}_breakfast",
                f"{date_str}_lunch",
                f"{date_str}_snack"
            ]

            if not all(header in current_headers for header in date_headers):
                new_dates.append(date_str)

        # Добавляем новые колонки
        if new_dates:
            for date_str in new_dates:
                ws.cell(1, ws.max_column + 1, f"{date_str}_breakfast")
                ws.cell(1, ws.max_column + 1, f"{date_str}_lunch")
                ws.cell(1, ws.max_column + 1, f"{date_str}_snack")

            # Добавляем пустые ячейки для существующих учеников
            for row in range(2, ws.max_row + 1):
                for _ in range(len(new_dates) * 3):
                    ws.cell(row, ws.max_column + 1, "")

        # Проверяем всех учеников из students.xlsx
        existing_ids = set()
        for row in range(2, ws.max_row + 1):
            existing_ids.add(str(ws.cell(row, 1).value))

        # Добавляем новых учеников
        for student in students:
            if student['id'] not in existing_ids:
                student_row = [student['id'], student['name'], student['class']]
                # Пустые ячейки для заказов
                for _ in range(ws.max_column - 3):
                    student_row.append("")
                ws.append(student_row)
                logger.info(f"Добавлен новый ученик: {student['name']} (ID: {student['id']})")

        wb.save(self.orders_path)
        logger.info(f"Обновлен файл orders.xlsx: добавлено {len(new_dates)} новых дат")

    def verify_student(self, student_id: str) -> Tuple[bool, Optional[StudentInfo]]:
        """Проверяет ученика по ID"""
        try:
            if not os.path.exists(self.students_path):
                return False, None

            wb = load_workbook(self.students_path, data_only=True)
            ws = wb.active

            for row in ws.iter_rows(min_row=2, values_only=True):
                if str(row[0]) == student_id:
                    return True, StudentInfo(
                        student_id=str(row[0]),
                        full_name=row[1],
                        class_name=row[2] if len(row) > 2 else ""
                    )

        except Exception as e:
            logger.error(f"Ошибка проверки ученика: {e}")

        return False, None

    def save_order(self, student_id: str, date_str: str, meals: Dict[str, bool]) -> bool:
        """Сохраняет заказ ученика"""
        try:
            # 1. Сохраняем в orders.xlsx
            wb = load_workbook(self.orders_path)
            ws = wb.active

            # Находим строку ученика
            student_row = None
            for r in range(2, ws.max_row + 1):
                if str(ws.cell(r, 1).value) == student_id:
                    student_row = r
                    break

            if not student_row:
                logger.error(f"Ученик {student_id} не найден в orders.xlsx")
                return False

            # Находим колонки для даты
            breakfast_col = None
            lunch_col = None
            snack_col = None

            for col in range(4, ws.max_column + 1):
                header = ws.cell(1, col).value
                if header and date_str in str(header):
                    if "_breakfast" in str(header):
                        breakfast_col = col
                    elif "_lunch" in str(header):
                        lunch_col = col
                    elif "_snack" in str(header):
                        snack_col = col

            if not all([breakfast_col, lunch_col, snack_col]):
                logger.error(f"Не найдены колонки для даты {date_str}")
                logger.debug(f"Искали в заголовках: {date_str}")
                return False

            # Сохраняем заказы
            ws.cell(row=student_row, column=breakfast_col, value="✅" if meals.get('breakfast') else "")
            ws.cell(row=student_row, column=lunch_col, value="✅" if meals.get('lunch') else "")
            ws.cell(row=student_row, column=snack_col, value="✅" if meals.get('snack') else "")

            wb.save(self.orders_path)

            # 2. Обновляем шаблон
            ok, student = self.verify_student(student_id)
            if ok and student.full_name:
                if not self.template_manager.update_order(student.full_name, date_str, meals):
                    logger.warning(f"Не удалось обновить шаблон для {student.full_name}")
                else:
                    logger.info(f"Шаблон успешно обновлен для {student.full_name}")
            else:
                logger.error(f"Не удалось получить информацию об ученике {student_id}")

            logger.info(f"Заказ сохранен: ID {student_id} - {date_str}")
            return True

        except Exception as e:
            logger.error(f"Ошибка сохранения заказа: {e}", exc_info=True)
            return False

    def get_student_orders(self, student_id: str, date_str: str) -> Dict[str, bool]:
        """Получает заказы ученика на дату"""
        try:
            wb = load_workbook(self.orders_path, data_only=True)
            ws = wb.active

            # Находим строку ученика
            student_row = None
            for r in range(2, ws.max_row + 1):
                if str(ws.cell(r, 1).value) == student_id:
                    student_row = r
                    break

            if not student_row:
                return self._empty_meals()

            # Находим колонки для даты
            breakfast_col = None
            lunch_col = None
            snack_col = None

            for col in range(4, ws.max_column + 1):
                header = ws.cell(1, col).value
                if header and date_str in str(header):
                    if "_breakfast" in str(header):
                        breakfast_col = col
                    elif "_lunch" in str(header):
                        lunch_col = col
                    elif "_snack" in str(header):
                        snack_col = col

            if not all([breakfast_col, lunch_col, snack_col]):
                return self._empty_meals()

            # Получаем заказы
            orders = {
                'breakfast': ws.cell(row=student_row, column=breakfast_col).value == "✅",
                'lunch': ws.cell(row=student_row, column=lunch_col).value == "✅",
                'snack': ws.cell(row=student_row, column=snack_col).value == "✅"
            }

            return orders

        except Exception as e:
            logger.error(f"Ошибка получения заказов: {e}")
            return self._empty_meals()

    def _empty_meals(self) -> Dict[str, bool]:
        return {meal.value: False for meal in MealType}

    def count_for_date(self, date_str: str) -> Dict[str, int]:
        """Подсчет заказов на дату"""
        try:
            wb = load_workbook(self.orders_path, data_only=True)
            ws = wb.active

            # Находим колонки для даты
            breakfast_col = None
            lunch_col = None
            snack_col = None

            for col in range(4, ws.max_column + 1):
                header = ws.cell(1, col).value
                if header and date_str in str(header):
                    if "_breakfast" in str(header):
                        breakfast_col = col
                    elif "_lunch" in str(header):
                        lunch_col = col
                    elif "_snack" in str(header):
                        snack_col = col

            if not all([breakfast_col, lunch_col, snack_col]):
                return {meal.value: 0 for meal in MealType}

            # Подсчитываем
            counts = {meal.value: 0 for meal in MealType}
            for row in range(2, ws.max_row + 1):
                if ws.cell(row, breakfast_col).value == "✅":
                    counts['breakfast'] += 1
                if ws.cell(row, lunch_col).value == "✅":
                    counts['lunch'] += 1
                if ws.cell(row, snack_col).value == "✅":
                    counts['snack'] += 1

            return counts

        except Exception as e:
            logger.error(f"Ошибка подсчета заказов: {e}")
            return {meal.value: 0 for meal in MealType}

    def get_working_dates(self, count: int = 10) -> List[Dict[str, str]]:
        """Получает список рабочих дат"""
        dates = []
        today = datetime.now()
        added = 0
        current_date = today

        while added < count:
            if current_date.weekday() < 5:  # Только будни
                dates.append({
                    'date_str': current_date.strftime("%Y-%m-%d"),
                    'display': f"{current_date.strftime('%d.%m')} ({DAY_NAMES_RU[current_date.weekday()]})"
                })
                added += 1
            current_date += timedelta(days=1)

        return dates

    def is_date_locked(self, date_str: str) -> bool:
        """Проверяет, можно ли редактировать заказ на дату"""
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            today = datetime.now().date()

            if date_obj.date() < today:
                return True

            if date_obj.date() == today and datetime.now().time() >= Config.DEADLINE_TIME:
                return True

            return False
        except:
            return True


# ================== КНОПКИ ==================
class KB:
    @staticmethod
    def main():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Ввести ID ученика", callback_data="input_id")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")]
        ])

    @staticmethod
    def dates(dates_list: List[Dict[str, str]]):
        keyboard = []
        for date_info in dates_list:
            keyboard.append([
                InlineKeyboardButton(
                    date_info['display'],
                    callback_data=f"date|{date_info['date_str']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def meals(date_str: str, current_orders: Dict[str, bool], is_locked: bool):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        date_display = f"{date_obj.strftime('%d.%m.%Y')} ({DAY_NAMES_RU[date_obj.weekday()]})"

        if is_locked:
            text = f"📅 {date_display}\n🔒 Редактирование закрыто\n\nТекущий заказ:"
            buttons = [
                [InlineKeyboardButton(f"Завтрак: {'✅' if current_orders['breakfast'] else '❌'}", callback_data="view")],
                [InlineKeyboardButton(f"Обед: {'✅' if current_orders['lunch'] else '❌'}", callback_data="view")],
                [InlineKeyboardButton(f"Полдник: {'✅' if current_orders['snack'] else '❌'}", callback_data="view")],
                [InlineKeyboardButton("⬅️ К датам", callback_data="back_dates")]
            ]
        else:
            text = f"📅 {date_display}\n✅ Можно редактировать\n\nВыберите питание:"
            buttons = [
                [
                    InlineKeyboardButton(
                        f"{'✅ ' if current_orders['breakfast'] else ''}Завтрак",
                        callback_data=f"meal|{date_str}|breakfast"
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅ ' if current_orders['lunch'] else ''}Обед",
                        callback_data=f"meal|{date_str}|lunch"
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅ ' if current_orders['snack'] else ''}Полдник",
                        callback_data=f"meal|{date_str}|snack"
                    )
                ],
                [
                    InlineKeyboardButton("✅ Всё на день", callback_data=f"all_day|{date_str}"),
                    InlineKeyboardButton("❌ Отменить на день", callback_data=f"none_day|{date_str}")
                ],
                [
                    InlineKeyboardButton("📅 Вся неделя", callback_data=f"all_week|{date_str}"),
                    InlineKeyboardButton("🗑️ Очистить неделю", callback_data=f"clear_week|{date_str}")
                ],
                [InlineKeyboardButton("⬅️ К датам", callback_data="back_dates")]
            ]

        return InlineKeyboardMarkup(buttons)

    @staticmethod
    def stats(is_admin: bool):
        buttons = []
        if is_admin:
            buttons.append([
                InlineKeyboardButton("📥 Скачать orders.xlsx", callback_data="download_orders")
            ])
            buttons.append([
                InlineKeyboardButton("📋 Скачать шаблон", callback_data="download_template")
            ])
            buttons.append([
                InlineKeyboardButton("🔄 Обновить данные", callback_data="refresh_data")
            ])
            # buttons.append([
            #     InlineKeyboardButton("🐛 Отладка", callback_data="debug_info")
            # ])
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
        return InlineKeyboardMarkup(buttons)


# ================== БОТ ==================
class FoodBot:
    INPUT_ID, DATES, MEALS = range(3)

    def __init__(self):
        self.db = Database()
        self.user_sessions = {}  # user_id -> session_data

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        self.user_sessions[user_id] = {'state': 'main'}

        await update.message.reply_text(
            "🏫 **Система заказа школьного питания**\n\n"
            "Выберите действие:",
            parse_mode='Markdown',
            reply_markup=KB.main()
        )

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        data = query.data

        # Обработка основных команд
        if data == "input_id":
            await query.edit_message_text(
                "🔑 **Введите ID ученика**\n\n"
                "ID можно получить у классного руководителя.\n"
                "Пример ID: 100953, 572477, 565546 и т.д.\n\n"
                "**Введите ID:**",
                parse_mode='Markdown'
            )
            return self.INPUT_ID

        elif data == "stats":
            if user_id not in Config.ADMIN_IDS:
                await query.edit_message_text(
                    "❌ У вас нет доступа к статистике",
                    reply_markup=KB.main()
                )
                return

            # Получаем статистику
            today = datetime.now().strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

            today_stats = self.db.count_for_date(today)
            tomorrow_stats = self.db.count_for_date(tomorrow)

            text = (
                "📊 **Статистика заказов**\n\n"
                f"**Сегодня ({datetime.now().strftime('%d.%m')}):**\n"
                f"🍳 Завтрак: {today_stats['breakfast']}\n"
                f"🍲 Обед: {today_stats['lunch']}\n"
                f"🥪 Полдник: {today_stats['snack']}\n\n"
                f"**Завтра ({datetime.fromisoformat(tomorrow).strftime('%d.%m')}):**\n"
                f"🍳 Завтрак: {tomorrow_stats['breakfast']}\n"
                f"🍲 Обед: {tomorrow_stats['lunch']}\n"
                f"🥪 Полдник: {tomorrow_stats['snack']}"
            )

            await query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=KB.stats(is_admin=True)
            )

        elif data == "download_orders":
            if user_id not in Config.ADMIN_IDS:
                return

            if os.path.exists(self.db.orders_path):
                await query.message.reply_document(
                    document=open(self.db.orders_path, 'rb'),
                    filename="orders.xlsx",
                    caption="📊 Файл заказов"
                )
            else:
                await query.message.reply_text("❌ Файл orders.xlsx не найден")

        elif data == "download_template":
            if user_id not in Config.ADMIN_IDS:
                return

            if os.path.exists(self.db.template_path):
                await query.message.reply_document(
                    document=open(self.db.template_path, 'rb'),
                    filename=Config.TEMPLATE_FILE,
                    caption="📋 Основной шаблон"
                )
            else:
                await query.message.reply_text(f"❌ Файл {Config.TEMPLATE_FILE} не найден")

        elif data == "refresh_data":
            if user_id not in Config.ADMIN_IDS:
                return

            # Перезагружаем шаблон
            if self.db.template_manager.load_template():
                await self._send_temp_message(
                    query.message.chat_id,
                    "✅ Данные обновлены",
                    context
                )
            else:
                await self._send_temp_message(
                    query.message.chat_id,
                    "❌ Ошибка обновления данных",
                    context
                )

        elif data == "debug_info":
            if user_id not in Config.ADMIN_IDS:
                return

            debug_text = self._get_debug_info()
            await query.message.reply_text(
                debug_text,
                parse_mode='Markdown'
            )

        elif data == "back_main":
            if user_id in self.user_sessions:
                self.user_sessions[user_id] = {'state': 'main'}

            await query.edit_message_text(
                "🏫 **Система заказа школьного питания**\n\n"
                "Выберите действие:",
                parse_mode='Markdown',
                reply_markup=KB.main()
            )

        elif data == "back_dates":
            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                await query.edit_message_text(
                    "❌ Сессия устарела. Начните заново.",
                    reply_markup=KB.main()
                )
                return

            dates = self.db.get_working_dates(10)
            student_info = self.user_sessions[user_id]

            await query.edit_message_text(
                f"👤 **{student_info['student_name']}**\n"
                f"🏫 Класс: {student_info['class_name']}\n\n"
                f"Выберите дату:",
                parse_mode='Markdown',
                reply_markup=KB.dates(dates)
            )
            return self.DATES

        elif data.startswith("date|"):
            date_str = data.split("|")[1]

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                await query.edit_message_text(
                    "❌ Сессия устарела. Начните заново.",
                    reply_markup=KB.main()
                )
                return

            student_info = self.user_sessions[user_id]
            orders = self.db.get_student_orders(student_info['student_id'], date_str)
            is_locked = self.db.is_date_locked(date_str)

            await query.edit_message_text(
                f"📅 **{datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')}**\n"
                f"👤 {student_info['student_name']}\n"
                f"🏫 {student_info['class_name']}\n\n"
                f"{'🔒 Редактирование закрыто' if is_locked else '✅ Можно редактировать'}",
                parse_mode='Markdown',
                reply_markup=KB.meals(date_str, orders, is_locked)
            )
            return self.MEALS

        elif data.startswith("meal|"):
            _, date_str, meal_type = data.split("|")

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                return

            student_info = self.user_sessions[user_id]

            # Проверяем можно ли редактировать
            if self.db.is_date_locked(date_str):
                await self._send_temp_message(
                    query.message.chat_id,
                    "⛔ Редактирование заказов на эту дату закрыто",
                    context
                )
                return self.MEALS

            # Получаем и обновляем заказы
            orders = self.db.get_student_orders(student_info['student_id'], date_str)
            orders[meal_type] = not orders[meal_type]

            # Сохраняем
            if self.db.save_order(student_info['student_id'], date_str, orders):
                await query.edit_message_reply_markup(
                    KB.meals(date_str, orders, False)
                )
                await self._send_temp_message(
                    query.message.chat_id,
                    "✅ Заказ обновлен",
                    context
                )
            else:
                await self._send_temp_message(
                    query.message.chat_id,
                    "❌ Ошибка сохранения заказа",
                    context
                )

        elif data.startswith("all_day|"):
            date_str = data.split("|")[1]

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                return

            if self.db.is_date_locked(date_str):
                await self._send_temp_message(
                    query.message.chat_id,
                    "⛔ Редактирование заказов на эту дату закрыто",
                    context
                )
                return self.MEALS

            # Заказываем всё на день
            orders = {meal.value: True for meal in MealType}

            if self.db.save_order(self.user_sessions[user_id]['student_id'], date_str, orders):
                await query.edit_message_reply_markup(
                    KB.meals(date_str, orders, False)
                )
                await self._send_temp_message(
                    query.message.chat_id,
                    "✅ Заказано всё питание на день",
                    context
                )

        elif data.startswith("none_day|"):
            date_str = data.split("|")[1]

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                return

            if self.db.is_date_locked(date_str):
                await self._send_temp_message(
                    query.message.chat_id,
                    "⛔ Редактирование заказов на эту дату закрыто",
                    context
                )
                return self.MEALS

            # Отменяем всё на день
            orders = {meal.value: False for meal in MealType}

            if self.db.save_order(self.user_sessions[user_id]['student_id'], date_str, orders):
                await query.edit_message_reply_markup(
                    KB.meals(date_str, orders, False)
                )
                await self._send_temp_message(
                    query.message.chat_id,
                    "❌ Питание на день отменено",
                    context
                )

        elif data.startswith("all_week|"):
            date_str = data.split("|")[1]

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                return

            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            monday = date_obj - timedelta(days=date_obj.weekday())

            success = 0
            total = 0

            for i in range(5):  # Понедельник - Пятница
                week_date = monday + timedelta(days=i)
                week_date_str = week_date.strftime("%Y-%m-%d")

                if self.db.is_date_locked(week_date_str):
                    continue

                total += 1
                orders = {meal.value: True for meal in MealType}

                if self.db.save_order(self.user_sessions[user_id]['student_id'], week_date_str, orders):
                    success += 1

            if success > 0:
                await self._send_temp_message(
                    query.message.chat_id,
                    f"✅ Заказано питание на {success} дней недели",
                    context
                )

            # Обновляем текущий день
            current_orders = self.db.get_student_orders(
                self.user_sessions[user_id]['student_id'], date_str
            )
            await query.edit_message_reply_markup(
                KB.meals(date_str, current_orders, self.db.is_date_locked(date_str))
            )

        elif data.startswith("clear_week|"):
            date_str = data.split("|")[1]

            if user_id not in self.user_sessions or 'student_id' not in self.user_sessions[user_id]:
                return

            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            monday = date_obj - timedelta(days=date_obj.weekday())

            success = 0
            total = 0

            for i in range(5):  # Понедельник - Пятница
                week_date = monday + timedelta(days=i)
                week_date_str = week_date.strftime("%Y-%m-%d")

                if self.db.is_date_locked(week_date_str):
                    continue

                total += 1
                orders = {meal.value: False for meal in MealType}

                if self.db.save_order(self.user_sessions[user_id]['student_id'], week_date_str, orders):
                    success += 1

            if success > 0:
                await self._send_temp_message(
                    query.message.chat_id,
                    f"❌ Питание отменено на {success} дней недели",
                    context
                )

            # Обновляем текущий день
            current_orders = self.db.get_student_orders(
                self.user_sessions[user_id]['student_id'], date_str
            )
            await query.edit_message_reply_markup(
                KB.meals(date_str, current_orders, self.db.is_date_locked(date_str))
            )

    async def input_id_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ввода ID ученика"""
        user_id = update.effective_user.id
        student_id = update.message.text.strip()

        # Проверяем ID (должен быть числовым)
        if not student_id.isdigit():
            await update.message.reply_text(
                "❌ **Неверный формат ID**\n\n"
                "ID должен состоять только из цифр.\n"
                "Пример правильного ID: 100953\n\n"
                "**Попробуйте снова:**",
                parse_mode='Markdown'
            )
            return self.INPUT_ID

        # Проверяем ID
        ok, student_info = self.db.verify_student(student_id)

        if not ok:
            await update.message.reply_text(
                "❌ **Ученик с таким ID не найден**\n\n"
                "Пожалуйста, проверьте ID и попробуйте снова.\n"
                "ID можно получить у классного руководителя.\n\n"
                "**Введите ID еще раз:**",
                parse_mode='Markdown'
            )
            return self.INPUT_ID

        # Сохраняем в сессию
        self.user_sessions[user_id] = {
            'student_id': student_id,
            'student_name': student_info.full_name,
            'class_name': student_info.class_name,
            'state': 'dates'
        }

        # Показываем доступные даты
        dates = self.db.get_working_dates(10)

        await update.message.reply_text(
            f"✅ **Ученик найден!**\n\n"
            f"👤 **{student_info.full_name}**\n"
            f"🏫 Класс: {student_info.class_name}\n"
            f"🔑 ID: {student_id}\n\n"
            f"Выберите дату:",
            parse_mode='Markdown',
            reply_markup=KB.dates(dates)
        )

        return self.DATES

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик отмены"""
        user_id = update.effective_user.id
        if user_id in self.user_sessions:
            self.user_sessions[user_id] = {'state': 'main'}

        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=KB.main()
        )
        return ConversationHandler.END

    async def _send_temp_message(self, chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE, delay: int = 2):
        """Отправляет временное сообщение"""
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except:
            pass

    def _get_debug_info(self) -> str:
        """Получает отладочную информацию"""
        debug_info = "🐛 **Отладочная информация**\n\n"

        # Информация о файлах
        debug_info += "📁 **Файлы:**\n"
        files_info = [
            (self.db.template_path, "Шаблон"),
            (self.db.orders_path, "Заказы"),
            (self.db.students_path, "Ученики")
        ]

        for file_path, name in files_info:
            if os.path.exists(file_path):
                size = os.path.getsize(file_path) / 1024
                debug_info += f"✅ {name}: {os.path.basename(file_path)} ({size:.1f} KB)\n"
            else:
                debug_info += f"❌ {name}: файл не найден\n"

        # Информация о шаблоне
        debug_info += "\n📋 **Шаблон:**\n"
        if self.db.template_manager.workbook:
            sheets = self.db.template_manager.workbook.sheetnames
            debug_info += f"Листов: {len(sheets)}\n"

            # Даты из шаблона
            dates = self.db.template_manager.get_all_dates()
            if dates:
                debug_info += f"Даты: {len(dates)} найдено\n"
                debug_info += f"Пример: {dates[0]} ... {dates[-1]}\n"
            else:
                debug_info += "❌ Даты не найдены\n"

            # Ученики из шаблона
            student_names = self.db.template_manager.get_all_students_names()
            debug_info += f"Ученики в шаблоне: {len(student_names)}\n"
        else:
            debug_info += "❌ Не загружен\n"

        # Информация о students.xlsx
        debug_info += "\n👥 **База учеников:**\n"
        if os.path.exists(self.db.students_path):
            try:
                wb = load_workbook(self.db.students_path, data_only=True)
                ws = wb.active
                student_count = ws.max_row - 1
                debug_info += f"Учеников: {student_count}\n"

                # Примеры ID
                sample_ids = []
                for row in range(2, min(6, ws.max_row + 1)):
                    student_id = ws.cell(row=row, column=1).value
                    if student_id:
                        sample_ids.append(str(student_id))

                if sample_ids:
                    debug_info += f"Примеры ID: {', '.join(sample_ids)}\n"
            except Exception as e:
                debug_info += f"❌ Ошибка: {str(e)}\n"
        else:
            debug_info += "❌ Файл не найден\n"

        # Информация о orders.xlsx
        debug_info += "\n📊 **Файл заказов:**\n"
        if os.path.exists(self.db.orders_path):
            try:
                wb = load_workbook(self.db.orders_path, data_only=True)
                ws = wb.active
                order_count = ws.max_row - 1
                date_count = (ws.max_column - 3) // 3
                debug_info += f"Учеников: {order_count}, Дат: {date_count}\n"
            except Exception as e:
                debug_info += f"❌ Ошибка: {str(e)}\n"
        else:
            debug_info += "❌ Файл не найден\n"

        # Активные сессии
        debug_info += f"\n👤 **Активные сессии:** {len(self.user_sessions)}\n"

        return debug_info

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Административные команды"""
        if update.effective_user.id not in Config.ADMIN_IDS:
            await update.message.reply_text("❌ У вас нет прав для этой команды")
            return

        command = update.message.text.lower()

        if command == "/reload":
            # Перезагружаем шаблон
            if self.db.template_manager.load_template():
                await update.message.reply_text("✅ Шаблон перезагружен")
            else:
                await update.message.reply_text("❌ Ошибка перезагрузки шаблона")

        elif command == "/check":
            debug_info = self._get_debug_info()
            await update.message.reply_text(debug_info, parse_mode='Markdown')


# ================== ЗАПУСК ==================
def main():
    """Основная функция запуска бота"""
    if not Config.BOT_TOKEN:
        logger.error("❌ Не указан BOT_TOKEN в конфигурации!")
        print("=" * 50)
        print("ВНИМАНИЕ: Не указан токен бота!")
        print("Добавьте в код строку: Config.BOT_TOKEN = 'ВАШ_ТОКЕН'")
        print("Получить токен можно у @BotFather в Telegram")
        print("=" * 50)
        return

    # Создаем приложение
    application = Application.builder().token(Config.BOT_TOKEN).build()

    # Создаем бота
    bot = FoodBot()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("cancel", bot.cancel))
    application.add_handler(CommandHandler(["reload", "check"], bot.admin_command))

    # Добавляем ConversationHandler для ввода ID
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bot.button_handler, pattern="^input_id$")
        ],
        states={
            bot.INPUT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot.input_id_handler)
            ],
            bot.DATES: [
                CallbackQueryHandler(bot.button_handler)
            ],
            bot.MEALS: [
                CallbackQueryHandler(bot.button_handler)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", bot.cancel),
            CallbackQueryHandler(bot.button_handler, pattern="^back_main$")
        ],
        allow_reentry=True
    )

    application.add_handler(conv_handler)

    # Обработчик остальных кнопок
    application.add_handler(CallbackQueryHandler(bot.button_handler))

    # Запускаем бота
    logger.info("🤖 Бот запускается...")
    print("\n" + "=" * 50)
    print("🏫 Школьный бот питания")
    print("=" * 50)
    print(f"Папка с данными: {Config.DATA_DIR}/")
    print(f"Файл учеников: {Config.STUDENTS_FILE}")
    print(f"Файл заказов: {Config.ORDERS_FILE}")
    print(f"Шаблон: {Config.TEMPLATE_FILE}")
    print("=" * 50)
    print("Проверка файлов...")

    # Проверяем необходимые файлы
    required_files = [
        (Config.STUDENTS_FILE, "students.xlsx с учениками"),
    ]

    all_ok = True
    for file_name, description in required_files:
        file_path = os.path.join(Config.DATA_DIR, file_name)
        if os.path.exists(file_path):
            print(f"✅ {file_name}: найден")
        else:
            print(f"❌ {file_name}: не найден ({description})")
            all_ok = False

    if not all_ok:
        print("\n⚠️  Некоторые файлы не найдены!")
        print("Проверьте папку data/ и добавьте необходимые файлы")

    print("=" * 50)
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    print("=" * 50 + "\n")

    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")
        print(f"\n❌ Ошибка: {e}")


if __name__ == "__main__":
    main()