
#!/usr/bin/env python3
import logging
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Dict, List, Optional, Set

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# -------------------- STATES --------------------
REG_NAME, REG_PHONE, REG_OFFER, REG_PASSPORT = range(4)

(
    VAC_TITLE,
    VAC_HEADCOUNT,
    VAC_DATE,
    VAC_LOCATION_TEXT,
    VAC_WORKTIME,
    VAC_SALARY,
    VAC_DEPOSIT,
    VAC_GEO,
    VAC_REVIEW,
    VAC_EDIT_CHOICE,
    VAC_EDIT_VALUE,
) = range(10, 21)

MSG_TARGET_MODE, MSG_USER_ID, MSG_VACANCY_PICK, MSG_TEXT = range(30, 34)

# -------------------- DATABASE HELPERS --------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_db(db_path: str) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            username TEXT,
            offer_accepted INTEGER DEFAULT 0,
            passport_file_id TEXT,
            approved INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            vacancy_id INTEGER NOT NULL,
            name TEXT,
            phone TEXT,
            username TEXT,
            offer_accepted INTEGER DEFAULT 0,
            passport_file_id TEXT,
            status TEXT DEFAULT 'pending',
            rejection_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            headcount INTEGER,
            date_text TEXT,
            location_text TEXT,
            work_time TEXT,
            salary TEXT,
            deposit INTEGER,
            latitude REAL,
            longitude REAL,
            channel_message_id INTEGER DEFAULT 0,
            remaining INTEGER,
            created_at TEXT
        )
        """
    )

    cur.execute("PRAGMA table_info(vacancies)")
    vacancy_columns = [row[1] for row in cur.fetchall()]
    if "date_text" not in vacancy_columns:
        cur.execute("ALTER TABLE vacancies ADD COLUMN date_text TEXT DEFAULT ''")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            receipt_file_id TEXT,
            confirmed INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()


# -------------------- USER HELPERS --------------------
def get_user(db_path: str, user_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, name, phone, username, offer_accepted, passport_file_id, approved
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "user_id": row[0],
        "name": row[1],
        "phone": row[2],
        "username": row[3],
        "offer_accepted": bool(row[4]),
        "passport_file_id": row[5],
        "approved": bool(row[6]),
    }


def upsert_user(
    db_path: str,
    user_id: int,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    username: Optional[str] = None,
    offer_accepted: Optional[bool] = None,
    passport_file_id: Optional[str] = None,
    approved: Optional[bool] = None,
) -> None:
    existing = get_user(db_path, user_id)

    final_name = name if name is not None else (existing["name"] if existing else None)
    final_phone = phone if phone is not None else (existing["phone"] if existing else None)
    final_username = username if username is not None else (existing["username"] if existing else None)
    final_offer = (
        int(offer_accepted)
        if offer_accepted is not None
        else (1 if existing and existing["offer_accepted"] else 0)
    )
    final_passport = (
        passport_file_id
        if passport_file_id is not None
        else (existing["passport_file_id"] if existing else None)
    )
    final_approved = (
        int(approved)
        if approved is not None
        else (1 if existing and existing["approved"] else 0)
    )

    now = now_iso()
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (
            user_id, name, phone, username, offer_accepted,
            passport_file_id, approved, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            phone = excluded.phone,
            username = excluded.username,
            offer_accepted = excluded.offer_accepted,
            passport_file_id = excluded.passport_file_id,
            approved = excluded.approved,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            final_name,
            final_phone,
            final_username,
            final_offer,
            final_passport,
            final_approved,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


# -------------------- APPLICATION HELPERS --------------------
def create_application(
    db_path: str,
    user_id: int,
    vacancy_id: int,
    name: str,
    phone: str,
    username: Optional[str],
    offer_accepted: bool,
    passport_file_id: str,
) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO applications (
            user_id, vacancy_id, name, phone, username, offer_accepted,
            passport_file_id, status, rejection_reason, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
        """,
        (
            user_id,
            vacancy_id,
            name,
            phone,
            username,
            int(offer_accepted),
            passport_file_id,
            now_iso(),
            now_iso(),
        ),
    )
    app_id = cur.lastrowid
    conn.commit()
    conn.close()
    return app_id


def get_application(db_path: str, app_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, vacancy_id, name, phone, username, offer_accepted,
               passport_file_id, status, rejection_reason
        FROM applications
        WHERE id = ?
        """,
        (app_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "user_id": row[1],
        "vacancy_id": row[2],
        "name": row[3],
        "phone": row[4],
        "username": row[5],
        "offer_accepted": bool(row[6]),
        "passport_file_id": row[7],
        "status": row[8],
        "rejection_reason": row[9],
    }


def update_application_status(
    db_path: str,
    app_id: int,
    status: str,
    rejection_reason: Optional[str] = None,
) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE applications
        SET status = ?, rejection_reason = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, rejection_reason, now_iso(), app_id),
    )
    conn.commit()
    conn.close()


def get_pending_application_for_user_and_vacancy(
    db_path: str,
    user_id: int,
    vacancy_id: int,
) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, vacancy_id, name, phone, username, offer_accepted,
               passport_file_id, status, rejection_reason
        FROM applications
        WHERE user_id = ? AND vacancy_id = ? AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, vacancy_id),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "user_id": row[1],
        "vacancy_id": row[2],
        "name": row[3],
        "phone": row[4],
        "username": row[5],
        "offer_accepted": bool(row[6]),
        "passport_file_id": row[7],
        "status": row[8],
        "rejection_reason": row[9],
    }


def list_rejected_applications_for_vacancy(db_path: str, vacancy_id: int) -> List[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, vacancy_id, name, phone, username, offer_accepted,
               passport_file_id, status, rejection_reason
        FROM applications
        WHERE vacancy_id = ? AND status = 'rejected'
        ORDER BY id ASC
        """,
        (vacancy_id,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append(
            {
                "id": row[0],
                "user_id": row[1],
                "vacancy_id": row[2],
                "name": row[3],
                "phone": row[4],
                "username": row[5],
                "offer_accepted": bool(row[6]),
                "passport_file_id": row[7],
                "status": row[8],
                "rejection_reason": row[9],
            }
        )
    return result


# -------------------- VACANCY HELPERS --------------------
def create_vacancy(
    db_path: str,
    title: str,
    headcount: int,
    date_text: str,
    location_text: str,
    work_time: str,
    salary: str,
    deposit: int,
    latitude: Optional[float],
    longitude: Optional[float],
) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vacancies (
            title, headcount, date_text, location_text, work_time, salary, deposit,
            latitude, longitude, channel_message_id, remaining, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            title,
            headcount,
            date_text,
            location_text,
            work_time,
            salary,
            deposit,
            latitude,
            longitude,
            headcount,
            now_iso(),
        ),
    )
    vacancy_id = cur.lastrowid
    conn.commit()
    conn.close()
    return vacancy_id


def set_vacancy_channel_message_id(db_path: str, vacancy_id: int, message_id: int) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE vacancies SET channel_message_id = ? WHERE id = ?",
        (message_id, vacancy_id),
    )
    conn.commit()
    conn.close()


def get_vacancy(db_path: str, vacancy_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, headcount, date_text, location_text, work_time, salary,
               deposit, latitude, longitude, channel_message_id, remaining
        FROM vacancies
        WHERE id = ?
        """,
        (vacancy_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "title": row[1],
        "headcount": row[2],
        "date_text": row[3],
        "location_text": row[4],
        "work_time": row[5],
        "salary": row[6],
        "deposit": row[7],
        "latitude": row[8],
        "longitude": row[9],
        "channel_message_id": row[10],
        "remaining": row[11],
    }


def list_vacancies(db_path: str, limit: int = 50) -> List[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, headcount, date_text, location_text, work_time, salary,
               deposit, latitude, longitude, channel_message_id, remaining
        FROM vacancies
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append(
            {
                "id": row[0],
                "title": row[1],
                "headcount": row[2],
                "date_text": row[3],
                "location_text": row[4],
                "work_time": row[5],
                "salary": row[6],
                "deposit": row[7],
                "latitude": row[8],
                "longitude": row[9],
                "channel_message_id": row[10],
                "remaining": row[11],
            }
        )
    return result


def decrement_vacancy_remaining(db_path: str, vacancy_id: int) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE vacancies SET remaining = remaining - 1 WHERE id = ? AND remaining > 0",
        (vacancy_id,),
    )
    conn.commit()
    conn.close()


# -------------------- BOOKING HELPERS --------------------
def add_booking(db_path: str, vacancy_id: int, user_id: int) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings (vacancy_id, user_id, receipt_file_id, confirmed, created_at)
        VALUES (?, ?, NULL, 0, ?)
        """,
        (vacancy_id, user_id, now_iso()),
    )
    booking_id = cur.lastrowid
    conn.commit()
    conn.close()
    return booking_id


def get_booking(db_path: str, booking_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, vacancy_id, user_id, receipt_file_id, confirmed
        FROM bookings
        WHERE id = ?
        """,
        (booking_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "vacancy_id": row[1],
        "user_id": row[2],
        "receipt_file_id": row[3],
        "confirmed": bool(row[4]),
    }


def get_latest_booking_for_user_vacancy(
    db_path: str,
    user_id: int,
    vacancy_id: int,
) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, vacancy_id, user_id, receipt_file_id, confirmed
        FROM bookings
        WHERE user_id = ? AND vacancy_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, vacancy_id),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "vacancy_id": row[1],
        "user_id": row[2],
        "receipt_file_id": row[3],
        "confirmed": bool(row[4]),
    }


def get_latest_open_booking_for_user(db_path: str, user_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, vacancy_id, user_id, receipt_file_id, confirmed
        FROM bookings
        WHERE user_id = ? AND confirmed = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "vacancy_id": row[1],
        "user_id": row[2],
        "receipt_file_id": row[3],
        "confirmed": bool(row[4]),
    }


def set_booking_receipt(db_path: str, booking_id: int, file_id: str) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE bookings SET receipt_file_id = ? WHERE id = ?",
        (file_id, booking_id),
    )
    conn.commit()
    conn.close()


def clear_booking_receipt(db_path: str, booking_id: int) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE bookings SET receipt_file_id = NULL WHERE id = ?",
        (booking_id,),
    )
    conn.commit()
    conn.close()


def set_booking_confirmed(db_path: str, booking_id: int, confirmed: bool) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE bookings SET confirmed = ? WHERE id = ?",
        (int(confirmed), booking_id),
    )
    conn.commit()
    conn.close()


def list_confirmed_bookings_for_vacancy(db_path: str, vacancy_id: int) -> List[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.id, b.user_id, b.receipt_file_id, u.name, u.phone, u.username
        FROM bookings b
        LEFT JOIN users u ON u.user_id = b.user_id
        WHERE b.vacancy_id = ? AND b.confirmed = 1
        ORDER BY b.id ASC
        """,
        (vacancy_id,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append(
            {
                "booking_id": row[0],
                "user_id": row[1],
                "receipt_file_id": row[2],
                "name": row[3],
                "phone": row[4],
                "username": row[5],
            }
        )
    return result


def list_pending_bookings_for_vacancy(db_path: str, vacancy_id: int) -> List[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT b.id, b.user_id, b.receipt_file_id, u.name, u.phone, u.username
        FROM bookings b
        LEFT JOIN users u ON u.user_id = b.user_id
        WHERE b.vacancy_id = ? AND b.confirmed = 0 AND b.receipt_file_id IS NOT NULL
        ORDER BY b.id ASC
        """,
        (vacancy_id,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        result.append(
            {
                "booking_id": row[0],
                "user_id": row[1],
                "receipt_file_id": row[2],
                "name": row[3],
                "phone": row[4],
                "username": row[5],
            }
        )
    return result


class VacancyBot:
    def __init__(self) -> None:
        self.token = os.environ.get("BOT_TOKEN")
        if not self.token:
            raise RuntimeError("BOT_TOKEN environment variable not set")

        self.bot_username = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")
        if not self.bot_username:
            raise RuntimeError("BOT_USERNAME environment variable not set")

        admins = os.environ.get("ADMIN_IDS")
        if not admins:
            raise RuntimeError("ADMIN_IDS environment variable not set")
        self.admin_ids = [int(x.strip()) for x in admins.split(",") if x.strip()]

        channel_id_raw = os.environ.get("CHANNEL_ID")
        if not channel_id_raw:
            raise RuntimeError("CHANNEL_ID environment variable not set")
        self.channel_id = int(channel_id_raw.strip())

        self.channel_public_username = os.environ.get("CHANNEL_PUBLIC_USERNAME", "").strip().lstrip("@") or None
        self.card_number = os.environ.get("CARD_NUMBER", "")
        self.card_holder = os.environ.get("CARD_HOLDER", "")

        data_dir = os.environ.get("DATA_DIR", "data")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "vacancy_bot.sqlite3")
        init_db(self.db_path)

        self.pending_bookings: Dict[int, int] = {}

        self.admin_menu = ReplyKeyboardMarkup(
            [["Yangi vakansiya qo'shish", "Statistika"], ["Foydalanuvchiga yozish"]],
            resize_keyboard=True,
        )

        self.message_target_menu = ReplyKeyboardMarkup(
            [["Bitta foydalanuvchiga"], ["Bron qilganlarga", "Pendingdagilarga"], ["Bekor qilish"]],
            resize_keyboard=True,
        )

        self.application = (
            Application.builder()
            .token(self.token)
            .concurrent_updates(False)
            .build()
        )

        self._register_handlers()

    def _register_handlers(self) -> None:
        start_conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start_entry)],
            states={
                REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.reg_name)],
                REG_PHONE: [MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, self.reg_phone)],
                REG_OFFER: [CallbackQueryHandler(self.reg_offer_response, pattern=r"^(accept_offer|decline_offer)$")],
                REG_PASSPORT: [MessageHandler(filters.PHOTO, self.reg_passport)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            per_user=True,
            per_message=False,
        )
        self.application.add_handler(start_conv)

        vacancy_conv = ConversationHandler(
            entry_points=[
                CommandHandler(
                    "new_vacancy",
                    self.new_vacancy_entry,
                    filters=filters.User(user_id=self.admin_ids),
                ),
                MessageHandler(
                    filters.Regex("^Yangi vakansiya qo'shish$") & filters.User(user_id=self.admin_ids),
                    self.new_vacancy_entry,
                ),
            ],
            states={
                VAC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_title)],
                VAC_HEADCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_headcount)],
                VAC_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_date)],
                VAC_LOCATION_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_location_text)],
                VAC_WORKTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_work_time)],
                VAC_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_salary)],
                VAC_DEPOSIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_deposit)],
                VAC_GEO: [MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, self.vacancy_geo)],
                VAC_REVIEW: [CallbackQueryHandler(self.vacancy_review_callback, pattern=r"^vac_(approve|reject)$")],
                VAC_EDIT_CHOICE: [
                    CallbackQueryHandler(
                        self.vacancy_edit_choice_callback,
                        pattern=r"^vac_edit:(title|headcount|date_text|location_text|work_time|salary|deposit|geo|cancel)$",
                    )
                ],
                VAC_EDIT_VALUE: [MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, self.vacancy_edit_value)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            per_user=True,
            per_message=False,
        )
        self.application.add_handler(vacancy_conv)

        message_conv = ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex("^Foydalanuvchiga yozish$") & filters.User(user_id=self.admin_ids),
                    self.message_entry,
                ),
                CommandHandler("message", self.message_entry, filters=filters.User(user_id=self.admin_ids)),
            ],
            states={
                MSG_TARGET_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_target_mode)],
                MSG_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_user_id)],
                MSG_VACANCY_PICK: [CallbackQueryHandler(self.message_vacancy_pick_callback, pattern=r"^msg_vac:(\d+)$")],
                MSG_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.message_text)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            per_user=True,
            per_message=False,
        )
        self.application.add_handler(message_conv)

        self.application.add_handler(
            MessageHandler(filters.Regex("^Statistika$") & filters.User(user_id=self.admin_ids), self.stats_entry)
        )
        self.application.add_handler(
            CommandHandler("stats", self.stats_entry, filters=filters.User(user_id=self.admin_ids))
        )

        self.application.add_handler(CallbackQueryHandler(self.stats_vacancy_callback, pattern=r"^stat_vac:(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.show_receipt_callback, pattern=r"^show_receipt:(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_approve_registration, pattern=r"^approve_app:(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_reject_registration, pattern=r"^reject_app:(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_confirm_receipt, pattern=r"^confirm_receipt:(\d+):(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_decline_receipt, pattern=r"^decline_receipt:(\d+):(\d+)$"))

        self.application.add_handler(MessageHandler(filters.PHOTO, self.global_photo_handler))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.generic_text_handler))
        self.application.add_error_handler(self.error_handler)

    # -------------------- CORE HELPERS --------------------
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def build_vacancy_caption(self, data: Dict) -> str:
        return (
            f"#{data['headcount']} vakansiya\n\n"
            f"Vakansiya nomi: {data['title']}\n"
            f"Necha kishi kerak: {data['headcount']}\n"
            f"Sana: {data['date_text']}\n"
            f"Lokatsiya (yozma): {data['location_text']}\n"
            f"Ish vaqti: {data['work_time']}\n"
            f"Xizmati haqi: {data['salary']}\n"
            f"Bron qilish narxi: {data['deposit']} so'm\n"
        )

    def build_channel_post_link(self, message_id: int) -> str:
        if self.channel_public_username:
            return f"https://t.me/{self.channel_public_username}/{message_id}"
        internal = str(abs(self.channel_id))
        if internal.startswith("100"):
            internal = internal[3:]
        return f"https://t.me/c/{internal}/{message_id}"

    def build_user_line(self, user_id: int, name: Optional[str], phone: Optional[str], username: Optional[str]) -> str:
        display = escape(name or username or f"Foydalanuvchi {user_id}")
        mention = f'<a href="tg://user?id={user_id}">{display}</a>'
        parts = [mention]
        if phone:
            parts.append(escape(phone))
        if username:
            uname = escape(username.lstrip("@"))
            parts.append(f'<a href="https://t.me/{uname}">@{uname}</a>')
        return " – ".join(parts)

    async def show_vacancy_preview(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        data = context.user_data["vacancy_draft"]
        caption = "📋 Vakansiya preview:\n\n" + self.build_vacancy_caption(data)
        caption += "\n📍 Geolokatsiya qo'shilgan" if data.get("latitude") is not None else "\n📍 Geolokatsiya yo'q"

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Tasdiqlayman", callback_data="vac_approve"),
                InlineKeyboardButton("Tasdiqlamayman", callback_data="vac_reject"),
            ]]
        )
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=keyboard)

    async def publish_vacancy_from_draft(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
        data = context.user_data["vacancy_draft"]

        vac_id = create_vacancy(
            self.db_path,
            title=data["title"],
            headcount=data["headcount"],
            date_text=data["date_text"],
            location_text=data["location_text"],
            work_time=data["work_time"],
            salary=data["salary"],
            deposit=data["deposit"],
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "Ishni bron qilish",
                    url=f"https://t.me/{self.bot_username}?start=book_{vac_id}",
                )
            ]]
        )

        msg: Message = await context.bot.send_message(
            chat_id=self.channel_id,
            text=self.build_vacancy_caption(data),
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

        set_vacancy_channel_message_id(self.db_path, vac_id, msg.message_id)

        if data.get("latitude") is not None and data.get("longitude") is not None:
            await context.bot.send_location(
                chat_id=self.channel_id,
                latitude=data["latitude"],
                longitude=data["longitude"],
            )

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Vakansiya kanalga yuborildi (ID: {vac_id}).",
            reply_markup=self.admin_menu,
        )
        return vac_id

    async def send_booking_payment_message(
        self,
        chat_id: int,
        vacancy_id: int,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        vacancy = get_vacancy(self.db_path, vacancy_id)
        if not vacancy:
            await context.bot.send_message(chat_id=chat_id, text="Vakansiya topilmadi.")
            return

        if vacancy["remaining"] <= 0:
            await context.bot.send_message(chat_id=chat_id, text="Kechirasiz, bu vakansiyada joy qolmagan.")
            return

        existing = get_latest_booking_for_user_vacancy(self.db_path, user_id, vacancy_id)

        if existing:
            if existing["confirmed"]:
                await self.send_confirmed_booking_details(user_id, vacancy_id, context)
                return

            if existing["receipt_file_id"]:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Siz bu vakansiya uchun chek yuborgansiz. Admin tasdig'ini kuting.",
                )
                return

            booking_id = existing["id"]
        else:
            booking_id = add_booking(self.db_path, vacancy_id, user_id)

        self.pending_bookings[user_id] = booking_id

        text = (
            f"Vakansiya: {vacancy['title']}\n"
            f"Sana: {vacancy['date_text']}\n"
            f"Lokatsiya: {vacancy['location_text']}\n"
            f"Ish vaqti: {vacancy['work_time']}\n"
            f"Xizmati haqi: {vacancy['salary']}\n"
            f"Bron qilish narxi: {vacancy['deposit']} so'm\n\n"
            f"Ishni bron qilishingiz uchun quyidagi kartaga bron summasini o'tkazing:\n"
            f"{self.card_number}\n"
            f"{self.card_holder}\n\n"
            f"Chekni shu botga rasm qilib yuboring."
        )
        await context.bot.send_message(chat_id=chat_id, text=text)

    async def send_welcome_and_continue(self, app_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        app = get_application(self.db_path, app_id)
        if not app:
            return

        name = escape(app["name"] or "foydalanuvchi")
        await context.bot.send_message(
            chat_id=app["user_id"],
            text=(
                f"Hurmatli {name} sizni Kunlik vakansiya jamoasiga qabul qilinganingiz bilan tabriklayman.\n\n"
                f"Quyida ish bilan batafsil tanishishingiz va bron qilishingiz mumkin!"
            ),
            parse_mode=ParseMode.HTML,
        )

        await self.send_booking_payment_message(
            chat_id=app["user_id"],
            vacancy_id=app["vacancy_id"],
            user_id=app["user_id"],
            context=context,
        )

    async def send_confirmed_booking_details(
        self,
        user_id: int,
        vacancy_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        user = get_user(self.db_path, user_id)
        vacancy = get_vacancy(self.db_path, vacancy_id)
        if not user or not vacancy:
            return

        link = self.build_channel_post_link(vacancy["channel_message_id"])
        vacancy_name_link = f'<a href="{link}">{escape(vacancy["title"])}</a>'

        text = (
            f"Hurmatli {escape(user['name'] or 'foydalanuvchi')}, siz {vacancy_name_link} ishi uchun joy bron qildingiz.\n\n"
            f"Pastda qo'shimcha tafsilotlar va lokatsiya yuboryapman, iltimos aytilgan joyga kechikmay keling!"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=self.channel_id,
                message_id=vacancy["channel_message_id"],
            )
        except Exception as exc:
            logger.warning(f"copy_message failed: {exc}")
            await context.bot.send_message(chat_id=user_id, text=self.build_vacancy_caption(vacancy))

        if vacancy.get("latitude") is not None and vacancy.get("longitude") is not None:
            await context.bot.send_location(
                chat_id=user_id,
                latitude=vacancy["latitude"],
                longitude=vacancy["longitude"],
            )

    async def send_vacancy_remaining_notice(self, vacancy_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        vacancy = get_vacancy(self.db_path, vacancy_id)
        if not vacancy:
            return

        if vacancy["remaining"] <= 0:
            try:
                await context.bot.edit_message_text(
                    chat_id=self.channel_id,
                    message_id=vacancy["channel_message_id"],
                    text=self.build_vacancy_caption(vacancy) + "\n❌ Ish joylari qolmadi! Rahmat )",
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(f"channel post edit failed: {exc}")
        else:
            try:
                await context.bot.send_message(
                    chat_id=self.channel_id,
                    text=f"Vakansiya uchun {vacancy['remaining']}/{vacancy['headcount']} bo'sh joy qoldi. Shoshiling!",
                    reply_to_message_id=vacancy["channel_message_id"],
                )
            except Exception as exc:
                logger.warning(f"remaining notice failed: {exc}")

    async def send_stats_for_vacancy(self, chat_id: int, vacancy_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        vacancy = get_vacancy(self.db_path, vacancy_id)
        if not vacancy:
            await context.bot.send_message(chat_id=chat_id, text="Vakansiya topilmadi.")
            return

        confirmed = list_confirmed_bookings_for_vacancy(self.db_path, vacancy_id)
        pending = list_pending_bookings_for_vacancy(self.db_path, vacancy_id)
        rejected = list_rejected_applications_for_vacancy(self.db_path, vacancy_id)

        lines: List[str] = []

        if vacancy["remaining"] <= 0:
            lines.append("<b>Bu vakansiya bo'yicha ishchilar ro'yxati to'lgan:</b>")
            lines.append(f"<b>{escape(vacancy['title'])}</b>")
            lines.append(f"Sana: {escape(vacancy['date_text'])}")
        else:
            lines.append(f"<b>{escape(vacancy['title'])}</b>")
            lines.append(f"Sana: {escape(vacancy['date_text'])}")
            lines.append(f"Bo'sh joy: {vacancy['remaining']}/{vacancy['headcount']}")

        lines.append("")
        lines.append(f"<b>1. Bron qilganlar soni:</b> {len(confirmed)}")
        if confirmed:
            for idx, item in enumerate(confirmed, start=1):
                lines.append(
                    f"– bron qilgan foydalanuvchi #{idx}: {self.build_user_line(item['user_id'], item['name'], item['phone'], item['username'])}"
                )
        else:
            lines.append("– yo'q")

        lines.append("")
        lines.append(f"<b>2. Pendingda turganlar soni:</b> {len(pending)}")
        if pending:
            for idx, item in enumerate(pending, start=1):
                lines.append(
                    f"– pending foydalanuvchi #{idx}: {self.build_user_line(item['user_id'], item['name'], item['phone'], item['username'])}"
                )
        else:
            lines.append("– yo'q")

        lines.append("")
        lines.append(f"<b>3. To'g'ri kelmaganlar soni:</b> {len(rejected)}")
        if rejected:
            for idx, item in enumerate(rejected, start=1):
                lines.append(
                    f"– rad etilgan foydalanuvchi #{idx}: {self.build_user_line(item['user_id'], item['name'], item['phone'], item['username'])}"
                )
                if item.get("rejection_reason"):
                    lines.append(f"  Sabab: {escape(item['rejection_reason'])}")
        else:
            lines.append("– yo'q")

        buttons: List[List[InlineKeyboardButton]] = []
        receipt_buttons: List[InlineKeyboardButton] = []

        for idx, item in enumerate(confirmed, start=1):
            receipt_buttons.append(
                InlineKeyboardButton(f"✅ Chek {idx}", callback_data=f"show_receipt:{item['booking_id']}")
            )
        for idx, item in enumerate(pending, start=1):
            receipt_buttons.append(
                InlineKeyboardButton(f"⏳ Pending chek {idx}", callback_data=f"show_receipt:{item['booking_id']}")
            )

        if receipt_buttons:
            for i in range(0, len(receipt_buttons), 2):
                buttons.append(receipt_buttons[i:i+2])
            lines.append("")
            lines.append("Cheklarni ko'rish uchun pastdagi tugmalardan foydalaning.")

        await context.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )

    async def show_vacancy_choice_for_message(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        vacancies = list_vacancies(self.db_path, limit=50)
        if not vacancies:
            await context.bot.send_message(chat_id=chat_id, text="Hali vakansiyalar yo'q.", reply_markup=self.admin_menu)
            return

        rows = []
        for vac in vacancies:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{vac['id']}. {vac['title']} ({vac['remaining']}/{vac['headcount']})",
                        callback_data=f"msg_vac:{vac['id']}",
                    )
                ]
            )
        await context.bot.send_message(
            chat_id=chat_id,
            text="Vakansiyani tanlang:",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def send_bulk_message(self, user_ids: List[int], text: str, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, int]:
        ok = 0
        fail = 0
        sent_to: Set[int] = set()

        for user_id in user_ids:
            if user_id in sent_to:
                continue
            try:
                await context.bot.send_message(chat_id=user_id, text=text)
                ok += 1
                sent_to.add(user_id)
            except Exception as exc:
                logger.warning("Bulk send failed to %s: %s", user_id, exc)
                fail += 1
        return {"ok": ok, "fail": fail}

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Unhandled error: %s", context.error)

    # -------------------- GENERAL --------------------
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if update.message:
            if self.is_admin(update.effective_user.id):
                await update.message.reply_text("Jarayon bekor qilindi.", reply_markup=self.admin_menu)
            else:
                await update.message.reply_text("Jarayon bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    async def start_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return ConversationHandler.END

        user = update.effective_user
        payload = context.args[0] if context.args else None
        logger.info("START payload from user %s: %s", user.id, payload)

        if payload and payload.startswith("book_"):
            try:
                vacancy_id = int(payload.split("_", 1)[1])
            except ValueError:
                await update.message.reply_text("Vakansiya ma'lumoti noto'g'ri.")
                return ConversationHandler.END

            user_record = get_user(self.db_path, user.id)

            if user_record and user_record.get("approved"):
                await self.send_booking_payment_message(
                    chat_id=update.message.chat_id,
                    vacancy_id=vacancy_id,
                    user_id=user.id,
                    context=context,
                )
                return ConversationHandler.END

            pending_app = get_pending_application_for_user_and_vacancy(self.db_path, user.id, vacancy_id)
            if pending_app:
                await update.message.reply_text("Sizning ma'lumotlaringiz yuborilgan. Admin tasdig'ini kuting.")
                return ConversationHandler.END

            context.user_data["pending_vacancy_id"] = vacancy_id
            await update.message.reply_text("Ism va familiyangizni kiriting:")
            return REG_NAME

        if self.is_admin(user.id):
            await update.message.reply_text(
                "Assalomu alaykum, admin! Quyidagi tugmalardan foydalaning.",
                reply_markup=self.admin_menu,
            )
        else:
            await update.message.reply_text(
                "Assalomu alaykum! 👋\n\n"
                "Bu bot orqali kunlik vakansiyalarni ko'rishingiz va ishni bron qilishingiz mumkin.\n"
                "Kanaldagi 'Ishni bron qilish' tugmasini bosing."
            )
        return ConversationHandler.END

    # -------------------- VACANCY FLOW --------------------
    async def new_vacancy_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return ConversationHandler.END

        context.user_data["vacancy_draft"] = {
            "title": "",
            "headcount": 0,
            "date_text": "",
            "location_text": "",
            "work_time": "",
            "salary": "",
            "deposit": 0,
            "latitude": None,
            "longitude": None,
        }
        await update.message.reply_text("Vakansiya nomini kiriting:", reply_markup=ReplyKeyboardRemove())
        return VAC_TITLE

    async def vacancy_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vacancy_draft"]["title"] = update.message.text.strip()
        await update.message.reply_text("Necha kishi kerak (raqam)?")
        return VAC_HEADCOUNT

    async def vacancy_headcount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            value = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Iltimos, raqam kiriting.")
            return VAC_HEADCOUNT

        context.user_data["vacancy_draft"]["headcount"] = value
        await update.message.reply_text("Sana kiriting (masalan: 5-aprel 2026 yoki 05.04.2026):")
        return VAC_DATE

    async def vacancy_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vacancy_draft"]["date_text"] = update.message.text.strip()
        await update.message.reply_text("Lokatsiya (yozma) kiriting:")
        return VAC_LOCATION_TEXT

    async def vacancy_location_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vacancy_draft"]["location_text"] = update.message.text.strip()
        await update.message.reply_text("Ish vaqti:")
        return VAC_WORKTIME

    async def vacancy_work_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vacancy_draft"]["work_time"] = update.message.text.strip()
        await update.message.reply_text("Xizmati haqi:")
        return VAC_SALARY

    async def vacancy_salary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vacancy_draft"]["salary"] = update.message.text.strip()
        await update.message.reply_text("Bron qilish narxi (faqat raqam):")
        return VAC_DEPOSIT

    async def vacancy_deposit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            value = int(update.message.text.strip().replace(" ", ""))
        except ValueError:
            await update.message.reply_text("Bron narxini faqat raqam bilan kiriting.")
            return VAC_DEPOSIT

        context.user_data["vacancy_draft"]["deposit"] = value
        await update.message.reply_text("Geolokatsiya yuboring yoki 'yo'q' deb yozing:")
        return VAC_GEO

    async def vacancy_geo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.location:
            context.user_data["vacancy_draft"]["latitude"] = update.message.location.latitude
            context.user_data["vacancy_draft"]["longitude"] = update.message.location.longitude
        elif update.message.text and update.message.text.strip().lower() == "yo'q":
            context.user_data["vacancy_draft"]["latitude"] = None
            context.user_data["vacancy_draft"]["longitude"] = None
        else:
            await update.message.reply_text("Iltimos, geopoziya yuboring yoki 'yo'q' deb yozing.")
            return VAC_GEO

        await self.show_vacancy_preview(update.effective_chat.id, context)
        return VAC_REVIEW

    async def vacancy_review_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "vac_approve":
            await self.publish_vacancy_from_draft(query.message.chat_id, context)
            return ConversationHandler.END

        field_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Vakansiya nomi", callback_data="vac_edit:title")],
                [InlineKeyboardButton("Necha kishi kerak", callback_data="vac_edit:headcount")],
                [InlineKeyboardButton("Sana", callback_data="vac_edit:date_text")],
                [InlineKeyboardButton("Lokatsiya (yozma)", callback_data="vac_edit:location_text")],
                [InlineKeyboardButton("Ish vaqti", callback_data="vac_edit:work_time")],
                [InlineKeyboardButton("Xizmati haqi", callback_data="vac_edit:salary")],
                [InlineKeyboardButton("Bron qilish narxi", callback_data="vac_edit:deposit")],
                [InlineKeyboardButton("Geolokatsiya", callback_data="vac_edit:geo")],
                [InlineKeyboardButton("Bekor qilish", callback_data="vac_edit:cancel")],
            ]
        )
        await query.message.reply_text(
            "Qaysi qismini o'zgartirmoqchisiz?",
            reply_markup=field_keyboard,
        )
        return VAC_EDIT_CHOICE

    async def vacancy_edit_choice_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        choice = query.data.split(":", 1)[1]

        if choice == "cancel":
            await self.show_vacancy_preview(query.message.chat_id, context)
            return VAC_REVIEW

        context.user_data["vacancy_edit_field"] = choice
        prompts = {
            "title": "Yangi vakansiya nomini kiriting:",
            "headcount": "Yangi kishi sonini kiriting:",
            "date_text": "Yangi sanani kiriting:",
            "location_text": "Yangi lokatsiya (yozma) ni kiriting:",
            "work_time": "Yangi ish vaqtini kiriting:",
            "salary": "Yangi xizmat haqini kiriting:",
            "deposit": "Yangi bron narxini kiriting:",
            "geo": "Yangi geolokatsiyani yuboring yoki 'yo'q' deb yozing:",
        }
        await query.message.reply_text(prompts[choice])
        return VAC_EDIT_VALUE

    async def vacancy_edit_value(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        field = context.user_data.get("vacancy_edit_field")
        draft = context.user_data["vacancy_draft"]

        if field == "geo":
            if update.message.location:
                draft["latitude"] = update.message.location.latitude
                draft["longitude"] = update.message.location.longitude
            elif update.message.text and update.message.text.strip().lower() == "yo'q":
                draft["latitude"] = None
                draft["longitude"] = None
            else:
                await update.message.reply_text("Geolokatsiya yuboring yoki 'yo'q' deb yozing.")
                return VAC_EDIT_VALUE
        elif field == "headcount":
            try:
                draft["headcount"] = int(update.message.text.strip())
            except ValueError:
                await update.message.reply_text("Iltimos, raqam kiriting.")
                return VAC_EDIT_VALUE
        elif field == "deposit":
            try:
                draft["deposit"] = int(update.message.text.strip().replace(" ", ""))
            except ValueError:
                await update.message.reply_text("Iltimos, raqam kiriting.")
                return VAC_EDIT_VALUE
        else:
            draft[field] = update.message.text.strip()

        await self.show_vacancy_preview(update.effective_chat.id, context)
        return VAC_REVIEW

    # -------------------- REGISTRATION FLOW --------------------
    async def reg_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["reg_name"] = update.message.text.strip()

        contact_button = KeyboardButton(
            text="Telefon raqamni yuborish",
            request_contact=True,
        )
        markup = ReplyKeyboardMarkup(
            [[contact_button]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(
            "Telefon raqamingizni yuboring yoki qo'lda kiriting:",
            reply_markup=markup,
        )
        return REG_PHONE

    async def reg_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.contact:
            phone = update.message.contact.phone_number
        else:
            phone = update.message.text.strip()

        context.user_data["reg_phone"] = phone

        await update.message.reply_text(
            "Telefon raqami qabul qilindi.",
            reply_markup=ReplyKeyboardRemove(),
        )

        offer_text = (
            "📝 Foydalanuvchi ofertasi\n\n"
            "\"Kunlik vakansiya\" markazi tomonidan taqdim etiladigan xizmatlar va undan foydalanuvchilar orasidagi shartnoma:\n\n"
            "1. Umumiy qoidalar.\n\n"
            "Ushbu botdan foydalanish orqali siz quyidagi shartlarga rozilik bildirasiz.\n\n"
            "Bizning xizmat - kunlik ishlarga nomzodlarni ish beruvchilar bilan aloqasini o'rnatish.\n\n"
            "2. Xizmat haqi.\n\n"
            "Har bir ish e'lonida xizmat haqi miqdori alohida ko'rsatiladi.\n\n"
            "Nomzod ishga yozilishdan oldin ko'rsatilgan summani to'laydi va to'lov tasdig'ini (check) botga yuboradi. "
            "Qalbaki check yuborish qat'iyan taqiqlanadi, bunday holat aniqlansa u foydalanuvchiga qaytib ish berilmaydi.\n\n"
            "3. Majburiyatlar.\n\n"
            "To'lovdan so'ng nomzod ishga chiqishi shart. Sababsiz chiqmaslik xizmatdan chetlashtirishga olib keladi.\n\n"
            "Biz ish beruvchi va nomzod o'rtasidagi nizolarga hech bir ko'rinishda javobgar emasmiz, "
            "lekin imkon qadar yordam beramiz.\n\n"
            "4. Javobgarlik chegarasi.\n\n"
            "Ish haqi, ish joyi sharoiti va boshqa qo'shimcha kelishuvlar uchun faqat ish beruvchi javobgar.\n\n"
            "Bizning yagona vazifamiz - faqat kontakt bog'lash va e'lonlarni yetkazish xolos.\n\n"
            "5. Yakuniy shartlar.\n\n"
            "Oferta va qoidalar doimiy yangilanib boradi. Botdan foydalanish orqali siz ushbu shartlarga rozilik bildirgan bo'lasiz.\n\n"
            "Agar rozi bo'lsangiz, pastdagi 'Roziman' tugmasini bosing."
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Roziman", callback_data="accept_offer"),
                InlineKeyboardButton("Rad etaman", callback_data="decline_offer"),
            ]]
        )

        await update.message.reply_text(
            offer_text,
            reply_markup=keyboard,
        )
        return REG_OFFER

    async def reg_offer_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "decline_offer":
            await query.edit_message_text("Siz ofertani rad qildingiz. Jarayon bekor qilindi.")
            return ConversationHandler.END

        await query.edit_message_text("Passport rasmini yuboring:")
        return REG_PASSPORT

    async def reg_passport(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.photo:
            await update.message.reply_text("Iltimos, passport rasmini yuboring.")
            return REG_PASSPORT

        file_id = update.message.photo[-1].file_id
        user = update.effective_user
        vacancy_id = context.user_data.get("pending_vacancy_id")

        if not vacancy_id:
            await update.message.reply_text("Vakansiya aniqlanmadi. Qayta urinib ko'ring.")
            return ConversationHandler.END

        upsert_user(
            self.db_path,
            user_id=user.id,
            name=context.user_data.get("reg_name"),
            phone=context.user_data.get("reg_phone"),
            username=user.username,
            offer_accepted=True,
            passport_file_id=file_id,
            approved=False,
        )

        app_id = create_application(
            self.db_path,
            user_id=user.id,
            vacancy_id=vacancy_id,
            name=context.user_data.get("reg_name"),
            phone=context.user_data.get("reg_phone"),
            username=user.username,
            offer_accepted=True,
            passport_file_id=file_id,
        )

        vacancy = get_vacancy(self.db_path, vacancy_id)
        vacancy_name = vacancy["title"] if vacancy else f"Vakansiya #{vacancy_id}"

        text = (
            f"#{app_id} yangi foydalanuvchi arizasi:\n"
            f"Vakansiya: {vacancy_name}\n"
            f"Ismi: {context.user_data.get('reg_name')}\n"
            f"Telefon raqami: {context.user_data.get('reg_phone')}\n"
            f"Ommaviy ofertaga rozilik: rozi ✅\n"
            f"Passport surati: quyida\n"
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Tasdiqlayman", callback_data=f"approve_app:{app_id}"),
                InlineKeyboardButton("Tasdiqlamayman", callback_data=f"reject_app:{app_id}"),
            ]]
        )

        for admin_id in self.admin_ids:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=file_id)
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            except Exception as exc:
                logger.warning(f"Failed to notify admin {admin_id}: {exc}")

        await update.message.reply_text("Ma'lumotlaringiz yuborildi. Admin tasdig'ini kuting.")
        return ConversationHandler.END

    # -------------------- ADMIN APPLICATION REVIEW --------------------
    async def admin_approve_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            app_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        app = get_application(self.db_path, app_id)
        if not app:
            await query.message.reply_text("Ariza topilmadi.")
            return

        update_application_status(self.db_path, app_id, "approved", None)
        upsert_user(
            self.db_path,
            user_id=app["user_id"],
            name=app["name"],
            phone=app["phone"],
            username=app["username"],
            offer_accepted=app["offer_accepted"],
            passport_file_id=app["passport_file_id"],
            approved=True,
        )

        await self.send_welcome_and_continue(app_id, context)
        await query.message.edit_text("Foydalanuvchi tasdiqlandi.")

    async def admin_reject_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            app_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        app = get_application(self.db_path, app_id)
        if not app:
            await query.message.reply_text("Ariza topilmadi.")
            return

        context.chat_data["pending_rejection_app_id"] = app_id
        user_name = app["name"] or "foydalanuvchi"

        await query.message.reply_text(
            f"{user_name} Boss, nega bu foydalanuvchini tasdiqlamadingiz? Dal*ayobmisiz?\n\nSababni yozing:"
        )
        try:
            await query.message.delete()
        except Exception:
            pass

    # -------------------- STATS --------------------
    async def stats_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not self.is_admin(update.effective_user.id):
            return

        vacancies = list_vacancies(self.db_path, limit=50)
        if not vacancies:
            await update.message.reply_text("Hali vakansiyalar yo'q.", reply_markup=self.admin_menu)
            return

        buttons = []
        for vac in vacancies:
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{vac['id']}. {vac['title']} ({vac['remaining']}/{vac['headcount']})",
                        callback_data=f"stat_vac:{vac['id']}",
                    )
                ]
            )

        await update.message.reply_text(
            "Vakansiyani tanlang:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def stats_vacancy_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            vacancy_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        await self.send_stats_for_vacancy(query.message.chat_id, vacancy_id, context)

    async def show_receipt_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            booking_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        booking = get_booking(self.db_path, booking_id)
        if not booking or not booking.get("receipt_file_id"):
            await query.message.reply_text("Chek topilmadi.")
            return

        user = get_user(self.db_path, booking["user_id"])
        vacancy = get_vacancy(self.db_path, booking["vacancy_id"])

        caption = (
            f"Chek\n"
            f"Foydalanuvchi: {user['name'] if user else booking['user_id']}\n"
            f"Telefon: {user['phone'] if user else '-'}\n"
            f"Vakansiya: {vacancy['title'] if vacancy else booking['vacancy_id']}\n"
            f"Holat: {'tasdiqlangan' if booking['confirmed'] else 'pending'}"
        )

        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=booking["receipt_file_id"],
            caption=caption,
        )

    # -------------------- ADMIN MESSAGE FLOW --------------------
    async def message_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not self.is_admin(update.effective_user.id):
            return ConversationHandler.END

        await update.message.reply_text(
            "Kimga yozmoqchisiz?",
            reply_markup=self.message_target_menu,
        )
        return MSG_TARGET_MODE

    async def message_target_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()

        if text == "Bekor qilish":
            await update.message.reply_text("Bekor qilindi.", reply_markup=self.admin_menu)
            return ConversationHandler.END

        if text == "Bitta foydalanuvchiga":
            context.user_data["message_mode"] = "single"
            await update.message.reply_text(
                "Foydalanuvchi Telegram ID sini yuboring:",
                reply_markup=ReplyKeyboardRemove(),
            )
            return MSG_USER_ID

        if text == "Bron qilganlarga":
            context.user_data["message_mode"] = "confirmed"
            await self.show_vacancy_choice_for_message(update.effective_chat.id, context)
            return MSG_VACANCY_PICK

        if text == "Pendingdagilarga":
            context.user_data["message_mode"] = "pending"
            await self.show_vacancy_choice_for_message(update.effective_chat.id, context)
            return MSG_VACANCY_PICK

        await update.message.reply_text("Tugmalardan birini tanlang.", reply_markup=self.message_target_menu)
        return MSG_TARGET_MODE

    async def message_user_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Iltimos, raqam yuboring.")
            return MSG_USER_ID

        context.user_data["message_target_users"] = [user_id]
        await update.message.reply_text(
            "Yuboriladigan xabar matnini yozing:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return MSG_TEXT

    async def message_vacancy_pick_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        try:
            vacancy_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return MSG_VACANCY_PICK

        mode = context.user_data.get("message_mode")
        user_ids: List[int] = []

        if mode == "confirmed":
            user_ids = [item["user_id"] for item in list_confirmed_bookings_for_vacancy(self.db_path, vacancy_id)]
        elif mode == "pending":
            user_ids = [item["user_id"] for item in list_pending_bookings_for_vacancy(self.db_path, vacancy_id)]

        if not user_ids:
            await query.message.reply_text("Bu tanlov bo'yicha foydalanuvchilar topilmadi.", reply_markup=self.admin_menu)
            return ConversationHandler.END

        context.user_data["message_target_users"] = user_ids
        context.user_data["message_vacancy_id"] = vacancy_id

        await query.message.reply_text(
            "Yuboriladigan xabar matnini yozing:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return MSG_TEXT

    async def message_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        user_ids = context.user_data.get("message_target_users") or []

        if not user_ids:
            await update.message.reply_text("Foydalanuvchilar topilmadi.", reply_markup=self.admin_menu)
            return ConversationHandler.END

        result = await self.send_bulk_message(user_ids, text, context)

        await update.message.reply_text(
            f"Xabar yuborildi.\n\nYetib bordi: {result['ok']}\nXato bo'ldi: {result['fail']}",
            reply_markup=self.admin_menu,
        )
        return ConversationHandler.END

    # -------------------- GENERIC TEXT / PHOTO --------------------
    async def generic_text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if self.is_admin(user_id) and "pending_rejection_app_id" in context.chat_data:
            app_id = context.chat_data.pop("pending_rejection_app_id")
            reason = update.message.text.strip()

            app = get_application(self.db_path, app_id)
            if not app:
                await update.message.reply_text("Ariza topilmadi.", reply_markup=self.admin_menu)
                return

            update_application_status(self.db_path, app_id, "rejected", reason)

            try:
                await context.bot.send_message(
                    chat_id=app["user_id"],
                    text=(
                        f"Kechirasiz, {app['name']} siz “Kunlik vakansiya” shartlaridan o‘ta olmaganingiz uchun "
                        f"hozircha sizning arizangizni qabul qila olmaymiz.\n\n"
                        f"Sabab:\n{reason}"
                    ),
                )
            except Exception as exc:
                logger.warning(f"Failed to send rejection reason: {exc}")

            await update.message.reply_text("Rad etish sababi foydalanuvchiga yuborildi.", reply_markup=self.admin_menu)

    async def global_photo_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id
        booking_id = self.pending_bookings.get(user_id)

        if not booking_id:
            open_booking = get_latest_open_booking_for_user(self.db_path, user_id)
            if open_booking and not open_booking.get("receipt_file_id"):
                booking_id = open_booking["id"]

        if not booking_id:
            return

        receipt_file_id = update.message.photo[-1].file_id
        set_booking_receipt(self.db_path, booking_id, receipt_file_id)

        booking = get_booking(self.db_path, booking_id)
        if not booking:
            await update.message.reply_text("Bron ma'lumoti topilmadi.")
            return

        vacancy_id = booking["vacancy_id"]
        user = get_user(self.db_path, user_id)
        vacancy = get_vacancy(self.db_path, vacancy_id)

        text = (
            f"To'lov cheki\n"
            f"Foydalanuvchi: {user['name'] if user else user_id}\n"
            f"Telefon: {user['phone'] if user else '-'}\n"
            f"Vakansiya: {vacancy['title'] if vacancy else vacancy_id}\n"
            f"Booking ID: {booking_id}\n\n"
            f"Tasdiqlaysizmi?"
        )

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "Tasdiqlayman",
                    callback_data=f"confirm_receipt:{vacancy_id}:{booking_id}",
                ),
                InlineKeyboardButton(
                    "Tasdiqlamayman",
                    callback_data=f"decline_receipt:{vacancy_id}:{booking_id}",
                ),
            ]]
        )

        for admin_id in self.admin_ids:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=receipt_file_id)
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            except Exception as exc:
                logger.warning(f"Failed to notify admin {admin_id} about receipt: {exc}")

        self.pending_bookings.pop(user_id, None)
        await update.message.reply_text("Chekingiz qabul qilindi. Admin tasdig'ini kuting.")

    # -------------------- RECEIPT REVIEW --------------------
    async def admin_confirm_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            vacancy_id = int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        booking = get_booking(self.db_path, booking_id)
        if not booking:
            await query.message.reply_text("Booking topilmadi.")
            return

        set_booking_confirmed(self.db_path, booking_id, True)
        decrement_vacancy_remaining(self.db_path, vacancy_id)

        await self.send_confirmed_booking_details(booking["user_id"], vacancy_id, context)
        await self.send_vacancy_remaining_notice(vacancy_id, context)

        await query.message.edit_text("To'lov tasdiqlandi.")

    async def admin_decline_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_admin(query.from_user.id):
            return

        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        booking = get_booking(self.db_path, booking_id)
        if not booking:
            await query.message.reply_text("Booking topilmadi.")
            return

        clear_booking_receipt(self.db_path, booking_id)
        self.pending_bookings[booking["user_id"]] = booking_id

        try:
            await context.bot.send_message(
                chat_id=booking["user_id"],
                text="Kechirasiz, chek tasdiqlanmadi. Iltimos, qaytadan to'g'ri chek yuboring.",
            )
        except Exception as exc:
            logger.warning(f"Failed to notify user about declined receipt: {exc}")

        await query.message.edit_text("To'lov rad etildi.")

    # -------------------- RUN --------------------
    def run(self) -> None:
        logger.info("Bot started")
        self.application.run_polling(drop_pending_updates=True)


def main() -> None:
    bot = VacancyBot()
    bot.run()


if __name__ == "__main__":
    main()
