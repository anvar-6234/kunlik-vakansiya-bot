#!/usr/bin/env python3
"""
Telegram bot for managing daily vacancy announcements and user booking.

This bot implements the workflow described by the user:

* Administrators can publish new vacancies to a channel with a single command.
  The bot collects details (title, required headcount, location, work time,
  salary, deposit price, and GPS location) via a conversation and posts a
  formatted message to the configured channel with an inline "Book this job"
  button.

* Regular users can browse vacancies in the channel and press the booking
  button to register interest. If the user has not yet registered with
  the bot, a guided registration flow collects their name, phone number,
  acceptance of the public offer, and a passport photo. The registration
  request is forwarded to an administrator for approval.

* Administrators review each registration request. They receive a message
  summarising the user's details along with two inline buttons: approve
  or reject. Approving the user sends a welcome message and continues
  the booking process. Rejecting the user triggers a follow‑up question
  asking for the rejection reason; this reason is sent back to the user
  along with a polite explanation.

* Once a user is approved and the booking process continues, the bot
  sends the job details and the deposit amount to the user. The user
  transfers the deposit to a specified card number and uploads a photo
  of the receipt. The receipt is forwarded to the administrator for
  verification. Once verified, the vacancy's available headcount is
  decreased and, if all spots are taken, the original channel post is
  edited to indicate that no positions remain.

Data storage is handled by a lightweight SQLite database stored in the
"data" directory. Each vacancy, user and booking record has its own
table. The bot relies on the asynchronous version of python‑telegram‑bot
and should be run using Python 3.10 or later.

Configuration: set the following environment variables before running
this script (for example in a `.env` file or your hosting provider's
configuration panel):

```
BOT_TOKEN       – your bot's API token provided by @BotFather.
ADMIN_IDS       – comma‑separated list of Telegram user IDs who are
                   allowed to create vacancies and approve bookings.
CHANNEL_ID      – numeric identifier (with a minus sign) of the channel
                   where vacancy posts should be published.
CARD_NUMBER     – the card number where users should send the deposit.
CARD_HOLDER     – the name of the card holder.
DATA_DIR        – directory path where the SQLite database will be
                   stored (optional; defaults to "data").
```

This file can be deployed to any hosting provider that supports long‑
running Python processes. Ensure that required dependencies (see
requirements.txt) are installed and configure a persistent file system
for the data directory.

References:
* The python‑telegram‑bot `InlineKeyboardButton` example demonstrates
  how to send a message with inline buttons and handle callback queries
  with `CallbackQueryHandler`. It emphasises the need to answer
  callback queries and how to edit messages when a button is pressed
  【786582654264845†L450-L479】.
* The `ConversationHandler` documentation explains how to organise
  multi‑step interactions using entry points, state definitions, and
  fallbacks. It notes that each handler returns a state value to
  transition to the next step, or `ConversationHandler.END` to finish
  the conversation, and that conversations can timeout or be
  cancelled 【872882286778047†L450-L475】.
"""

import os
import json
import logging
import sqlite3
from datetime import datetime
from typing import Dict, Optional, Tuple

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# --- Database helpers ------------------------------------------------------

def init_db(db_path: str) -> None:
    """Initialise SQLite tables if they don't already exist."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Table storing users awaiting approval and approved users.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            offer_accepted INTEGER,
            passport_file_id TEXT,
            approved INTEGER DEFAULT 0,
            created_at TEXT
        )
        """
    )
    # Table storing vacancies.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vacancies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            headcount INTEGER,
            location_text TEXT,
            work_time TEXT,
            salary TEXT,
            deposit INTEGER,
            latitude REAL,
            longitude REAL,
            channel_message_id INTEGER,
            remaining INTEGER,
            created_at TEXT
        )
        """
    )
    # Table storing bookings. Each booking links a user to a vacancy and
    # records the deposit payment status.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id INTEGER,
            user_id INTEGER,
            receipt_file_id TEXT,
            confirmed INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY (vacancy_id) REFERENCES vacancies(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """
    )
    conn.commit()
    conn.close()


def get_user(db_path: str, user_id: int) -> Optional[Dict[str, any]]:
    """Retrieve a user record by Telegram user ID."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT user_id, name, phone, offer_accepted, passport_file_id, approved FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "user_id": row[0],
            "name": row[1],
            "phone": row[2],
            "offer_accepted": bool(row[3]),
            "passport_file_id": row[4],
            "approved": bool(row[5]),
        }
    return None


def upsert_user(db_path: str, user_id: int, name: str, phone: str, offer_accepted: bool, passport_file_id: str) -> None:
    """Insert or update a user record."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (user_id, name, phone, offer_accepted, passport_file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name=excluded.name,
            phone=excluded.phone,
            offer_accepted=excluded.offer_accepted,
            passport_file_id=excluded.passport_file_id
        """,
        (user_id, name, phone, int(offer_accepted), passport_file_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def set_user_approved(db_path: str, user_id: int, approved: bool) -> None:
    """Mark a user as approved or not approved."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE users SET approved = ? WHERE user_id = ?", (int(approved), user_id))
    conn.commit()
    conn.close()


def add_vacancy(
    db_path: str,
    title: str,
    headcount: int,
    location_text: str,
    work_time: str,
    salary: str,
    deposit: int,
    latitude: Optional[float],
    longitude: Optional[float],
    channel_message_id: int,
) -> int:
    """Insert a new vacancy and return its ID."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vacancies (
            title, headcount, location_text, work_time, salary, deposit,
            latitude, longitude, channel_message_id, remaining, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            headcount,
            location_text,
            work_time,
            salary,
            deposit,
            latitude,
            longitude,
            channel_message_id,
            headcount,
            datetime.utcnow().isoformat(),
        ),
    )
    vacancy_id = cur.lastrowid
    conn.commit()
    conn.close()
    return vacancy_id


def get_vacancy(db_path: str, vacancy_id: int) -> Optional[Dict[str, any]]:
    """Retrieve a vacancy by ID."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, headcount, location_text, work_time, salary, deposit, latitude, longitude, channel_message_id, remaining FROM vacancies WHERE id = ?",
        (vacancy_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row[0],
            "title": row[1],
            "headcount": row[2],
            "location_text": row[3],
            "work_time": row[4],
            "salary": row[5],
            "deposit": row[6],
            "latitude": row[7],
            "longitude": row[8],
            "channel_message_id": row[9],
            "remaining": row[10],
        }
    return None


def decrement_vacancy_remaining(db_path: str, vacancy_id: int) -> None:
    """Decrease the remaining count for a vacancy and return the new count."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE vacancies SET remaining = remaining - 1 WHERE id = ? AND remaining > 0",
        (vacancy_id,),
    )
    conn.commit()
    conn.close()


def get_vacancy_remaining(db_path: str, vacancy_id: int) -> int:
    """Retrieve the remaining headcount for a vacancy."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT remaining FROM vacancies WHERE id = ?", (vacancy_id,))
    (remaining,) = cur.fetchone()
    conn.close()
    return remaining


def add_booking(db_path: str, vacancy_id: int, user_id: int) -> int:
    """Create a booking record and return its ID."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bookings (vacancy_id, user_id, created_at) VALUES (?, ?, ?)",
        (vacancy_id, user_id, datetime.utcnow().isoformat()),
    )
    booking_id = cur.lastrowid
    conn.commit()
    conn.close()
    return booking_id


def set_booking_receipt(db_path: str, booking_id: int, file_id: str) -> None:
    """Attach a payment receipt to a booking."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET receipt_file_id = ? WHERE id = ?", (file_id, booking_id))
    conn.commit()
    conn.close()


def set_booking_confirmed(db_path: str, booking_id: int, confirmed: bool) -> None:
    """Mark a booking as confirmed (deposit verified) or not."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET confirmed = ? WHERE id = ?", (int(confirmed), booking_id))
    conn.commit()
    conn.close()


# --- Conversation states ---------------------------------------------------

# States for user registration flow
(REG_NAME, REG_PHONE, REG_OFFER, REG_PASSPORT) = range(4)

# States for new vacancy creation flow
(
    VAC_TITLE,
    VAC_HEADCOUNT,
    VAC_LOCATION_TEXT,
    VAC_WORKTIME,
    VAC_SALARY,
    VAC_DEPOSIT,
    VAC_GEO,
) = range(4, 11)


class VacancyBot:
    """Encapsulate bot logic and state."""

    def __init__(self) -> None:
        self.token = os.environ.get("BOT_TOKEN")
        if not self.token:
            raise RuntimeError("BOT_TOKEN environment variable not set")
        admins = os.environ.get("ADMIN_IDS")
        if not admins:
            raise RuntimeError("ADMIN_IDS environment variable not set")
        self.admin_ids = [int(x) for x in admins.split(",")]
        self.channel_id = int(os.environ.get("CHANNEL_ID", "0"))
        self.card_number = os.environ.get("CARD_NUMBER", "")
        self.card_holder = os.environ.get("CARD_HOLDER", "")
        # Database path
        data_dir = os.environ.get("DATA_DIR", "data")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "vacancy_bot.sqlite3")
        init_db(self.db_path)
        # Application
        self.application = Application.builder().token(self.token).build()
        # Register handlers
        self._register_handlers()

        # In‑memory state to remember which vacancy is being booked by a user or
        # processed by admin during receipt verification.
        self.pending_bookings: Dict[int, int] = {}  # user_id -> booking_id
        self.pending_receipts: Dict[int, Tuple[int, int]] = {}
        # message_id -> (vacancy_id, booking_id) for receipt approval (admin)

    # -------------------- Handler registration -----------------------------
    def _register_handlers(self) -> None:
        # Start command: greet user and prompt for registration or help
        self.application.add_handler(CommandHandler("start", self.start))

        # Conversation handler for new vacancy flow
        vacancy_conv = ConversationHandler(
            entry_points=[CommandHandler("new_vacancy", self.new_vacancy_entry, filters.User(user_id=self.admin_ids))],
            states={
                VAC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_title)],
                VAC_HEADCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_headcount)],
                VAC_LOCATION_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_location_text)],
                VAC_WORKTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_work_time)],
                VAC_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_salary)],
                VAC_DEPOSIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_deposit)],
                VAC_GEO: [MessageHandler(filters.LOCATION | filters.TEXT & ~filters.COMMAND, self.vacancy_geo)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        self.application.add_handler(vacancy_conv)

        # Conversation handler for user registration flow
        reg_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_registration, pattern=r"^register:(\d+)$")],
            states={
                REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.reg_name)],
                REG_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), self.reg_phone)],
                REG_OFFER: [CallbackQueryHandler(self.reg_offer_response, pattern=r"^(accept_offer|decline_offer)$")],
                REG_PASSPORT: [MessageHandler(filters.PHOTO, self.reg_passport)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )
        self.application.add_handler(reg_conv)

        # Callback for booking button on vacancy posts
        self.application.add_handler(CallbackQueryHandler(self.book_vacancy, pattern=r"^book:(\d+)$"))

        # Callback for admin approval of registration
        self.application.add_handler(CallbackQueryHandler(self.admin_approve_registration, pattern=r"^approve_reg:(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_reject_registration, pattern=r"^reject_reg:(\d+)$"))

        # Callback for admin approval of receipt
        self.application.add_handler(CallbackQueryHandler(self.admin_confirm_receipt, pattern=r"^confirm_receipt:(\d+):(\d+)$"))
        self.application.add_handler(CallbackQueryHandler(self.admin_decline_receipt, pattern=r"^decline_receipt:(\d+):(\d+)$"))

        # Catch other text messages (e.g., admin reply reasons)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.generic_text_handler))

    # -------------------- General handlers ---------------------------------
    async def start(self, update: Update, context: CallbackContext) -> None:
        """Welcome message on /start command."""
        user = update.effective_user
        if not user:
            return
        message = (
            "Assalomu alaykum! 👋\n\n"
            "Bu bot orqali kunlik vakansiyalarni ko'rishingiz va ishni bron qilishingiz mumkin."
            " Vakansiyalarni ko'rish uchun kanaldan e'lonlarni kuzatib boring va post ostidagi"
            " 'Ishni bron qilish' tugmasini bosing."
        )
        await update.message.reply_text(message)

    async def cancel(self, update: Update, context: CallbackContext) -> int:
        """Handle cancellation of any conversation."""
        await update.message.reply_text("Jarayon bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    # -------------------- Vacancy creation flow -----------------------------
    async def new_vacancy_entry(self, update: Update, context: CallbackContext) -> int:
        """Entry point for creating a new vacancy."""
        await update.message.reply_text("Vakansiya nomini kiriting:")
        return VAC_TITLE

    async def vacancy_title(self, update: Update, context: CallbackContext) -> int:
        context.user_data["vac_title"] = update.message.text.strip()
        await update.message.reply_text("Necha kishi kerak (raqam)?")
        return VAC_HEADCOUNT

    async def vacancy_headcount(self, update: Update, context: CallbackContext) -> int:
        text = update.message.text.strip()
        try:
            headcount = int(text)
        except ValueError:
            await update.message.reply_text("Iltimos, raqam kiriting (necha kishi kerak).")
            return VAC_HEADCOUNT
        context.user_data["vac_headcount"] = headcount
        await update.message.reply_text("Lokatsiya (yozma ko'rinishida) kiriting:")
        return VAC_LOCATION_TEXT

    async def vacancy_location_text(self, update: Update, context: CallbackContext) -> int:
        context.user_data["vac_location_text"] = update.message.text.strip()
        await update.message.reply_text("Ish vaqti (masalan: 09:00-18:00):")
        return VAC_WORKTIME

    async def vacancy_work_time(self, update: Update, context: CallbackContext) -> int:
        context.user_data["vac_work_time"] = update.message.text.strip()
        await update.message.reply_text("Xizmati haqi (o'zgaruvchi so'mda yoki raqam):")
        return VAC_SALARY

    async def vacancy_salary(self, update: Update, context: CallbackContext) -> int:
        context.user_data["vac_salary"] = update.message.text.strip()
        await update.message.reply_text("Bron qilish narxi (so'mda, faqat raqam):")
        return VAC_DEPOSIT

    async def vacancy_deposit(self, update: Update, context: CallbackContext) -> int:
        text = update.message.text.strip().replace(" ", "")
        try:
            deposit = int(text)
        except ValueError:
            await update.message.reply_text("Bron narxini faqat raqam sifatida kiriting.")
            return VAC_DEPOSIT
        context.user_data["vac_deposit"] = deposit
        await update.message.reply_text(
            "Lokatsiyani geopoziya sifatida yuboring yoki 'yo'q' deb yozing:"
        )
        return VAC_GEO

    async def vacancy_geo(self, update: Update, context: CallbackContext) -> int:
        latitude = None
        longitude = None
        if update.message.location:
            latitude = update.message.location.latitude
            longitude = update.message.location.longitude
        elif update.message.text and update.message.text.lower() == "yo'q":
            pass
        else:
            # If text is not 'yo'q', ask again
            await update.message.reply_text("Iltimos, geopoziya yuboring yoki 'yo'q' deb yozing.")
            return VAC_GEO
        context.user_data["vac_latitude"] = latitude
        context.user_data["vac_longitude"] = longitude

        # Compose post and send to channel
        title = context.user_data.get("vac_title")
        headcount = context.user_data.get("vac_headcount")
        loc_text = context.user_data.get("vac_location_text")
        work_time = context.user_data.get("vac_work_time")
        salary = context.user_data.get("vac_salary")
        deposit = context.user_data.get("vac_deposit")

        vac_id_temp = -1  # placeholder until saved in DB
        # Compose message text
        caption = (
            f"#{headcount} vakansiya\n\n"
            f"Vakansiya nomi: {title}\n"
            f"Necha kishi kerak: {headcount}\n"
            f"Lokatsiya (yozma): {loc_text}\n"
            f"Ish vaqti: {work_time}\n"
            f"Xizmati haqi: {salary}\n"
            f"Bron qilish narxi: {deposit} so'm\n"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Ishni bron qilish", callback_data=f"book:temp")],
            ]
        )
        # Send post to channel with inline button; we will edit callback_data later
        msg: Message = await context.bot.send_message(
            chat_id=self.channel_id,
            text=caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        # If location coordinates were provided, send as separate message
        if latitude is not None and longitude is not None:
            await context.bot.send_location(chat_id=self.channel_id, latitude=latitude, longitude=longitude)
        # Save vacancy in DB
        vac_id = add_vacancy(
            self.db_path,
            title=title,
            headcount=headcount,
            location_text=loc_text,
            work_time=work_time,
            salary=salary,
            deposit=deposit,
            latitude=latitude,
            longitude=longitude,
            channel_message_id=msg.message_id,
        )
        # Edit button callback_data to include actual vacancy ID
        new_keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Ishni bron qilish", callback_data=f"book:{vac_id}")],
            ]
        )
        await msg.edit_reply_markup(reply_markup=new_keyboard)
        await update.message.reply_text(
            f"Vakansiya kanalga yuborildi (ID: {vac_id}).", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    # -------------------- User registration flow ---------------------------
    async def start_registration(self, update: Update, context: CallbackContext) -> int:
        """Start registration when user clicks booking button and isn't registered."""
        query = update.callback_query
        await query.answer()
        # Extract vacancy id from callback_data
        data = query.data  # format register:<vacancy_id>
        try:
            _, vac_id_str = data.split(":")
            vacancy_id = int(vac_id_str)
        except (ValueError, IndexError):
            await query.edit_message_text("Noto'g'ri ma'lumot.")
            return ConversationHandler.END
        # Save vacancy id for this user
        context.user_data["pending_vacancy_id"] = vacancy_id
        # Ask for name and surname
        await query.message.reply_text("Ism va familiyangizni kiriting:")
        return REG_NAME

    async def reg_name(self, update: Update, context: CallbackContext) -> int:
        context.user_data["reg_name"] = update.message.text.strip()
        # Request phone number (request contact button)
        contact_button = KeyboardButton(text="Telefon raqamni yuborish", request_contact=True)
        markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
        await update.message.reply_text("Telefon raqamingizni yuboring yoki qo'lda kiriting:", reply_markup=markup)
        return REG_PHONE

    async def reg_phone(self, update: Update, context: CallbackContext) -> int:
        phone = None
        if update.message.contact:
            phone = update.message.contact.phone_number
        else:
            phone = update.message.text.strip()
        context.user_data["reg_phone"] = phone
        # Send public offer text and ask acceptance
        offer_text = (
            "⬇ Ommaviy oferta matni ⬇\n"
            "Hurmatli foydalanuvchi!\n\n"
            "Bu botdan foydalanish orqali siz taklif etiladigan xizmatlar va shartlarga rozilik bildirasiz."
            " Shartlarni to'liq o'qib, rozi bo'lsangiz 'Roziman' tugmasini bosing.\n"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Roziman", callback_data="accept_offer"), InlineKeyboardButton("Rad etaman", callback_data="decline_offer")],
            ]
        )
        await update.message.reply_text(offer_text, reply_markup=keyboard, reply_markup_message_id=None)
        return REG_OFFER

    async def reg_offer_response(self, update: Update, context: CallbackContext) -> int:
        query = update.callback_query
        await query.answer()
        if query.data == "decline_offer":
            await query.edit_message_text("Siz ommaviy ofertani rad etdingiz. Registratsiya bekor qilindi.")
            return ConversationHandler.END
        # Accept offer -> ask for passport photo
        await query.edit_message_text("Passport rasmini yuboring (faqat rasm):")
        return REG_PASSPORT

    async def reg_passport(self, update: Update, context: CallbackContext) -> int:
        # Save passport photo file_id
        photo = update.message.photo
        if not photo:
            await update.message.reply_text("Iltimos, passport rasmini yuboring.")
            return REG_PASSPORT
        file_id = photo[-1].file_id  # highest resolution
        context.user_data["reg_passport_file_id"] = file_id
        user_id = update.message.from_user.id
        # Save user in DB as unapproved
        name = context.user_data.get("reg_name")
        phone = context.user_data.get("reg_phone")
        upsert_user(self.db_path, user_id, name, phone, True, file_id)
        # Notify admin for approval
        vacancy_id = context.user_data.get("pending_vacancy_id")
        # Compose admin message
        text = (
            f"#{user_id} yangi foydalanuvchi:\n"
            f"Ismi: {name}\n"
            f"Telefon raqami: {phone}\n"
            f"Ommaviy ofertaga rozilik: rozi ✅\n"
            f"Passport surati: ⬇\n"
        )
        # Inline buttons for admin approval
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Tasdiqlayman", callback_data=f"approve_reg:{user_id}"),
                    InlineKeyboardButton("Tasdiqlamayman", callback_data=f"reject_reg:{user_id}"),
                ]
            ]
        )
        # Send photo then message with buttons to each admin privately
        for admin_id in self.admin_ids:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=file_id)
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            except Exception as exc:
                logger.warning(f"Failed to notify admin {admin_id}: {exc}")
        await update.message.reply_text(
            "Ma'lumotlaringiz yuborildi. Admin tasdig'ini kuting."
        )
        return ConversationHandler.END

    # -------------------- Booking logic ------------------------------------
    async def book_vacancy(self, update: Update, context: CallbackContext) -> None:
        """Handle 'Ishni bron qilish' button from channel posts."""
        query = update.callback_query
        await query.answer()
        data = query.data  # format: book:<vacancy_id>
        try:
            _, vac_id_str = data.split(":")
            vacancy_id = int(vac_id_str)
        except (ValueError, IndexError):
            await query.edit_message_text("Noto'g'ri ma'lumot.")
            return
        user_id = query.from_user.id
        user_record = get_user(self.db_path, user_id)
        if not user_record or not user_record.get("approved"):
            # Ask user to register; store vacancy id in callback_data for registration
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Ro'yhatdan o'tish", callback_data=f"register:{vacancy_id}")],
                ]
            )
            await query.message.reply_text(
                "Iltimos, ro'yhatdan o'tish jarayonidan o'ting.", reply_markup=keyboard
            )
            return
        # User approved: create booking record and ask to pay deposit
        booking_id = add_booking(self.db_path, vacancy_id, user_id)
        self.pending_bookings[user_id] = booking_id
        vacancy = get_vacancy(self.db_path, vacancy_id)
        deposit = vacancy.get("deposit")
        message = (
            f"Ishni bron qilish uchun {deposit} so'm miqdorida bron to'lovini quyidagi kartaga to'lang:\n\n"
            f"{self.card_number}\n{self.card_holder}\n\n"
            "To'lov chekin (skrinshot) yuboring."
        )
        await query.message.reply_text(message)

    # -------------------- Admin review flows -------------------------------
    async def admin_approve_registration(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        try:
            _, user_id_str = query.data.split(":")
            user_id = int(user_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return
        # Mark user as approved
        set_user_approved(self.db_path, user_id, True)
        # Send welcome message to user
        user = get_user(self.db_path, user_id)
        name = user.get("name") if user else ""
        vac_id = context.user_data.get("pending_vacancy_id")  # might not be accessible here
        welcome_msg = (
            f"Hurmatli {name}, sizni Kunlik vakansiya jamoasiga qabul qilinganingiz bilan tabriklaymiz.\n\n"
            "Quyida ish bilan batafsil tanishishingiz va bron qilishingiz mumkin!"
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=welcome_msg)
        except Exception as exc:
            logger.warning(f"Failed to send welcome message to {user_id}: {exc}")
        await query.message.edit_text("Foydalanuvchi tasdiqlandi.")

    async def admin_reject_registration(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        try:
            _, user_id_str = query.data.split(":")
            user_id = int(user_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return
        # Ask admin for reason; store user_id in chat_data
        context.chat_data["pending_rejection_user"] = user_id
        await query.message.reply_text(
            f"{{name}} Boss, nega bu foydalanuvchini tasdiqlamadingiz? Dal*ayobmisiz?\n\nSababni yozing:",
        )
        await query.message.delete()

    async def generic_text_handler(self, update: Update, context: CallbackContext) -> None:
        """Handle generic text messages; used for admin rejection reason and receipt verification."""
        # Check if an admin wrote a rejection reason
        if update.effective_user.id in self.admin_ids and "pending_rejection_user" in context.chat_data:
            user_id = context.chat_data.pop("pending_rejection_user")
            reason = update.message.text.strip()
            # Send to user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"Kechirasiz, siz “Kunlik vakansiya” shartlaridan o’ta olmaganingiz uchun hozircha arizangizni qabul qila olmaymiz.\n\n"
                        f"Sabab: {reason}"
                    ),
                )
            except Exception as exc:
                logger.warning(f"Failed to notify user {user_id} about rejection: {exc}")
            await update.message.reply_text("Foydalanuvchi rad etildi va sababi yuborildi.")
            return
        # Check if user is sending receipt for pending booking
        user_id = update.effective_user.id
        if user_id in self.pending_bookings and update.message.photo:
            booking_id = self.pending_bookings[user_id]
            receipt_id = update.message.photo[-1].file_id
            set_booking_receipt(self.db_path, booking_id, receipt_id)
            # Forward to admin(s) for verification
            vacancy_id = None
            # Retrieve vacancy id from booking
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT vacancy_id FROM bookings WHERE id = ?", (booking_id,))
            row = cur.fetchone()
            conn.close()
            if row:
                vacancy_id = row[0]
            # Compose admin message
            text = (
                f"To'lov cheki\nFoydalanuvchi ID: {user_id}\nBooking ID: {booking_id}\nVakansiya ID: {vacancy_id}\nTasdiqlaysizmi?"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Tasdiqlayman", callback_data=f"confirm_receipt:{vacancy_id}:{booking_id}"
                        ),
                        InlineKeyboardButton(
                            "Tasdiqlamayman", callback_data=f"decline_receipt:{vacancy_id}:{booking_id}"
                        ),
                    ]
                ]
            )
            for admin_id in self.admin_ids:
                try:
                    # Send photo and message
                    await context.bot.send_photo(chat_id=admin_id, photo=receipt_id)
                    msg = await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
                    # Store pending receipt mapping
                    self.pending_receipts[msg.message_id] = (vacancy_id, booking_id)
                except Exception as exc:
                    logger.warning(f"Failed to notify admin {admin_id} about receipt: {exc}")
            await update.message.reply_text("Chek yuborildi. Admin tasdig'ini kuting.")
            # Remove from pending booking list to prevent duplicates
            self.pending_bookings.pop(user_id, None)
            return

    async def admin_confirm_receipt(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            vacancy_id = int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return
        # Mark booking as confirmed
        set_booking_confirmed(self.db_path, booking_id, True)
        # Decrease vacancy remaining
        decrement_vacancy_remaining(self.db_path, vacancy_id)
        remaining = get_vacancy_remaining(self.db_path, vacancy_id)
        vacancy = get_vacancy(self.db_path, vacancy_id)
        # Edit channel post if needed
        if remaining <= 0:
            try:
                await context.bot.edit_message_text(
                    chat_id=self.channel_id,
                    message_id=vacancy.get("channel_message_id"),
                    text=(
                        f"#{vacancy.get('headcount')} vakansiya\n\n"
                        f"Vakansiya nomi: {vacancy.get('title')}\n"
                        f"Necha kishi kerak: {vacancy.get('headcount')}\n"
                        f"Lokatsiya (yozma): {vacancy.get('location_text')}\n"
                        f"Ish vaqti: {vacancy.get('work_time')}\n"
                        f"Xizmati haqi: {vacancy.get('salary')}\n"
                        f"Bron qilish narxi: {vacancy.get('deposit')} so'm\n\n"
                        "❌ Ish joylari qolmadi! Rahmat )"
                    ),
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(f"Failed to edit channel post: {exc}")
        else:
            # Notify channel that spots remain
            try:
                await context.bot.send_message(
                    chat_id=self.channel_id,
                    text=(
                        f"#{vacancy.get('headcount')} vakansiya – {remaining}/{vacancy.get('headcount')} joy qoldi!\n"
                        f"Shoshiling, bron qiling!"
                    ),
                    reply_to_message_id=vacancy.get("channel_message_id"),
                )
            except Exception as exc:
                logger.warning(f"Failed to send reminder to channel: {exc}")
        # Notify user
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM bookings WHERE id = ?", (booking_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            user_id = row[0]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Bron to'lovi tasdiqlandi. Ish siz uchun bron qilindi!",
                )
            except Exception as exc:
                logger.warning(f"Failed to notify user {user_id} about booking confirmation: {exc}")
        await query.message.edit_text("To'lov tasdiqlandi.")

    async def admin_decline_receipt(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            vacancy_id = int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return
        # Mark booking as not confirmed and allow user to resend receipt
        set_booking_confirmed(self.db_path, booking_id, False)
        # Notify user to resend
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM bookings WHERE id = ?", (booking_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            user_id = row[0]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Kechirasiz, to'lov cheki tasdiqlanmadi. Iltimos, to'g'ri chekin qayta yuboring.",
                )
            except Exception as exc:
                logger.warning(f"Failed to notify user {user_id} about receipt rejection: {exc}")
        await query.message.edit_text("To'lov rad etildi.")

    # -------------------- Runner ------------------------------------------
    def run(self) -> None:
        """Start the bot polling."""
        logger.info("Bot started")
        self.application.run_polling()


def main() -> None:
    bot = VacancyBot()
    bot.run()


if __name__ == "__main__":
    main()
