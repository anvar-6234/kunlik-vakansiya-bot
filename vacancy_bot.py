#!/usr/bin/env python3
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, Optional

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

(REG_NAME, REG_PHONE, REG_OFFER, REG_PASSPORT) = range(4)
(
    VAC_TITLE,
    VAC_HEADCOUNT,
    VAC_LOCATION_TEXT,
    VAC_WORKTIME,
    VAC_SALARY,
    VAC_DEPOSIT,
    VAC_GEO,
) = range(10, 17)


# -------------------- DATABASE --------------------


def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            offer_accepted INTEGER DEFAULT 0,
            passport_file_id TEXT,
            approved INTEGER DEFAULT 0,
            pending_vacancy_id INTEGER,
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


def get_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def get_user(db_path: str, user_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_id, name, phone, offer_accepted, passport_file_id, approved, pending_vacancy_id
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
        "offer_accepted": bool(row[3]),
        "passport_file_id": row[4],
        "approved": bool(row[5]),
        "pending_vacancy_id": row[6],
    }


def upsert_user(
    db_path: str,
    user_id: int,
    name: Optional[str] = None,
    phone: Optional[str] = None,
    offer_accepted: Optional[bool] = None,
    passport_file_id: Optional[str] = None,
    approved: Optional[bool] = None,
    pending_vacancy_id: Optional[int] = None,
) -> None:
    existing = get_user(db_path, user_id)

    final_name = name if name is not None else (existing["name"] if existing else None)
    final_phone = phone if phone is not None else (existing["phone"] if existing else None)
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
    final_pending_vacancy = (
        pending_vacancy_id
        if pending_vacancy_id is not None
        else (existing["pending_vacancy_id"] if existing else None)
    )

    now = datetime.utcnow().isoformat()

    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (
            user_id, name, phone, offer_accepted, passport_file_id,
            approved, pending_vacancy_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name,
            phone = excluded.phone,
            offer_accepted = excluded.offer_accepted,
            passport_file_id = excluded.passport_file_id,
            approved = excluded.approved,
            pending_vacancy_id = excluded.pending_vacancy_id,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            final_name,
            final_phone,
            final_offer,
            final_passport,
            final_approved,
            final_pending_vacancy,
            now,
            now,
        ),
    )
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
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO vacancies (
            title, headcount, location_text, work_time, salary, deposit,
            latitude, longitude, channel_message_id, remaining, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def get_vacancy(db_path: str, vacancy_id: int) -> Optional[Dict]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, title, headcount, location_text, work_time, salary, deposit,
               latitude, longitude, channel_message_id, remaining
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
        "location_text": row[3],
        "work_time": row[4],
        "salary": row[5],
        "deposit": row[6],
        "latitude": row[7],
        "longitude": row[8],
        "channel_message_id": row[9],
        "remaining": row[10],
    }


def decrement_vacancy_remaining(db_path: str, vacancy_id: int) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        "UPDATE vacancies SET remaining = remaining - 1 WHERE id = ? AND remaining > 0",
        (vacancy_id,),
    )
    conn.commit()
    conn.close()


def get_vacancy_remaining(db_path: str, vacancy_id: int) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("SELECT remaining FROM vacancies WHERE id = ?", (vacancy_id,))
    row = cur.fetchone()
    conn.close()
    return int(row[0]) if row else 0


def add_booking(db_path: str, vacancy_id: int, user_id: int) -> int:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bookings (vacancy_id, user_id, created_at)
        VALUES (?, ?, ?)
        """,
        (vacancy_id, user_id, datetime.utcnow().isoformat()),
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


def set_booking_receipt(db_path: str, booking_id: int, file_id: str) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET receipt_file_id = ? WHERE id = ?", (file_id, booking_id))
    conn.commit()
    conn.close()


def set_booking_confirmed(db_path: str, booking_id: int, confirmed: bool) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET confirmed = ? WHERE id = ?", (int(confirmed), booking_id))
    conn.commit()
    conn.close()


# -------------------- BOT --------------------


class VacancyBot:
    def __init__(self) -> None:
        self.token = os.environ.get("BOT_TOKEN")
        if not self.token:
            raise RuntimeError("BOT_TOKEN environment variable not set")

        self.bot_username = os.environ.get("BOT_USERNAME")
        if not self.bot_username:
            raise RuntimeError("BOT_USERNAME environment variable not set")

        admins = os.environ.get("ADMIN_IDS")
        if not admins:
            raise RuntimeError("ADMIN_IDS environment variable not set")

        channel_id_raw = os.environ.get("CHANNEL_ID")
        if not channel_id_raw:
            raise RuntimeError("CHANNEL_ID environment variable not set")

        self.admin_ids = [int(x.strip()) for x in admins.split(",") if x.strip()]
        self.channel_id = int(channel_id_raw.strip())
        self.card_number = os.environ.get("CARD_NUMBER", "")
        self.card_holder = os.environ.get("CARD_HOLDER", "")

        data_dir = os.environ.get("DATA_DIR", "data")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "vacancy_bot.sqlite3")
        init_db(self.db_path)

        self.application = Application.builder().token(self.token).build()

        self.pending_bookings: Dict[int, int] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        start_conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start_entry)],
            states={
                REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.reg_name)],
                REG_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), self.reg_phone)],
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
                CommandHandler("new_vacancy", self.new_vacancy_entry, filters=filters.User(user_id=self.admin_ids))
            ],
            states={
                VAC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_title)],
                VAC_HEADCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_headcount)],
                VAC_LOCATION_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_location_text)],
                VAC_WORKTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_work_time)],
                VAC_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_salary)],
                VAC_DEPOSIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.vacancy_deposit)],
                VAC_GEO: [MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, self.vacancy_geo)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            per_chat=True,
            per_user=True,
            per_message=False,
        )
        self.application.add_handler(vacancy_conv)

        self.application.add_handler(
            CallbackQueryHandler(self.admin_approve_registration, pattern=r"^approve_reg:(\d+)$")
        )
        self.application.add_handler(
            CallbackQueryHandler(self.admin_reject_registration, pattern=r"^reject_reg:(\d+)$")
        )
        self.application.add_handler(
            CallbackQueryHandler(self.admin_confirm_receipt, pattern=r"^confirm_receipt:(\d+):(\d+)$")
        )
        self.application.add_handler(
            CallbackQueryHandler(self.admin_decline_receipt, pattern=r"^decline_receipt:(\d+):(\d+)$")
        )

        self.application.add_handler(MessageHandler(filters.PHOTO, self.global_photo_handler))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.generic_text_handler))

    # -------------------- HELPERS --------------------

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

        booking_id = add_booking(self.db_path, vacancy_id, user_id)
        self.pending_bookings[user_id] = booking_id

        text = (
            f"Vakansiya: {vacancy['title']}\n"
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

    async def send_welcome_and_continue(
        self,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        user = get_user(self.db_path, user_id)
        if not user:
            return

        name = user.get("name") or "foydalanuvchi"
        pending_vacancy_id = user.get("pending_vacancy_id")

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"Hurmatli {name} sizni Kunlik vakansiya jamoasiga qabul qilinganingiz bilan tabriklayman.\n\n"
                f"Quyida ish bilan batafsil tanishishingiz va bron qilishingiz mumkin!"
            ),
        )

        if pending_vacancy_id:
            await self.send_booking_payment_message(
                chat_id=user_id,
                vacancy_id=pending_vacancy_id,
                user_id=user_id,
                context=context,
            )

    async def send_vacancy_remaining_notice(
        self,
        vacancy_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        vacancy = get_vacancy(self.db_path, vacancy_id)
        if not vacancy:
            return

        remaining = vacancy["remaining"]
        if remaining <= 0:
            try:
                await context.bot.edit_message_text(
                    chat_id=self.channel_id,
                    message_id=vacancy["channel_message_id"],
                    text=(
                        f"#{vacancy['headcount']} vakansiya\n\n"
                        f"Vakansiya nomi: {vacancy['title']}\n"
                        f"Necha kishi kerak: {vacancy['headcount']}\n"
                        f"Lokatsiya (yozma): {vacancy['location_text']}\n"
                        f"Ish vaqti: {vacancy['work_time']}\n"
                        f"Xizmati haqi: {vacancy['salary']}\n"
                        f"Bron qilish narxi: {vacancy['deposit']} so'm\n\n"
                        f"❌ Ish joylari qolmadi! Rahmat )"
                    ),
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(f"Channel post edit failed: {exc}")
        else:
            try:
                await context.bot.send_message(
                    chat_id=self.channel_id,
                    text=f"Vakansiya uchun {remaining}/{vacancy['headcount']} bo'sh joy qoldi. Shoshiling!",
                    reply_to_message_id=vacancy["channel_message_id"],
                )
            except Exception as exc:
                logger.warning(f"Remaining notice failed: {exc}")

    # -------------------- GENERAL --------------------

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if update.message:
            await update.message.reply_text("Jarayon bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    async def start_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return ConversationHandler.END

        user = update.effective_user
        payload = context.args[0] if context.args else None

        if payload and payload.startswith("book_"):
            try:
                vacancy_id = int(payload.split("_", 1)[1])
            except ValueError:
                await update.message.reply_text("Vakansiya ma'lumoti noto'g'ri.")
                return ConversationHandler.END

            context.user_data["pending_vacancy_id"] = vacancy_id

            user_record = get_user(self.db_path, user.id)

            if user_record and user_record.get("approved"):
                await self.send_booking_payment_message(
                    chat_id=update.message.chat_id,
                    vacancy_id=vacancy_id,
                    user_id=user.id,
                    context=context,
                )
                return ConversationHandler.END

            if user_record and user_record.get("passport_file_id") and not user_record.get("approved"):
                upsert_user(self.db_path, user.id, pending_vacancy_id=vacancy_id)
                await update.message.reply_text(
                    "Sizning ma'lumotlaringiz oldin yuborilgan. Admin tasdig'ini kuting."
                )
                return ConversationHandler.END

            await update.message.reply_text("Ism va familiyangizni kiriting:")
            return REG_NAME

        await update.message.reply_text(
            "Assalomu alaykum! 👋\n\n"
            "Bu bot orqali kunlik vakansiyalarni ko'rishingiz va ishni bron qilishingiz mumkin. "
            "Vakansiyalarni ko'rish uchun kanaldagi e'lon ostidagi 'Ishni bron qilish' tugmasini bosing."
        )
        return ConversationHandler.END

    # -------------------- VACANCY CREATION --------------------

    async def new_vacancy_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return ConversationHandler.END
        await update.message.reply_text("Vakansiya nomini kiriting:")
        return VAC_TITLE

    async def vacancy_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vac_title"] = update.message.text.strip()
        await update.message.reply_text("Necha kishi kerak (raqam)?")
        return VAC_HEADCOUNT

    async def vacancy_headcount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            headcount = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Iltimos, raqam kiriting.")
            return VAC_HEADCOUNT

        context.user_data["vac_headcount"] = headcount
        await update.message.reply_text("Lokatsiya (yozma) kiriting:")
        return VAC_LOCATION_TEXT

    async def vacancy_location_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vac_location_text"] = update.message.text.strip()
        await update.message.reply_text("Ish vaqti:")
        return VAC_WORKTIME

    async def vacancy_work_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vac_work_time"] = update.message.text.strip()
        await update.message.reply_text("Xizmati haqi:")
        return VAC_SALARY

    async def vacancy_salary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["vac_salary"] = update.message.text.strip()
        await update.message.reply_text("Bron qilish narxi (faqat raqam):")
        return VAC_DEPOSIT

    async def vacancy_deposit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip().replace(" ", "")
        try:
            deposit = int(text)
        except ValueError:
            await update.message.reply_text("Bron narxini faqat raqam bilan kiriting.")
            return VAC_DEPOSIT

        context.user_data["vac_deposit"] = deposit
        await update.message.reply_text("Geolokatsiya yuboring yoki 'yo'q' deb yozing:")
        return VAC_GEO

    async def vacancy_geo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        latitude = None
        longitude = None

        if update.message.location:
            latitude = update.message.location.latitude
            longitude = update.message.location.longitude
        elif update.message.text and update.message.text.strip().lower() == "yo'q":
            pass
        else:
            await update.message.reply_text("Iltimos, geopoziya yuboring yoki 'yo'q' deb yozing.")
            return VAC_GEO

        title = context.user_data.get("vac_title")
        headcount = context.user_data.get("vac_headcount")
        loc_text = context.user_data.get("vac_location_text")
        work_time = context.user_data.get("vac_work_time")
        salary = context.user_data.get("vac_salary")
        deposit = context.user_data.get("vac_deposit")

        caption = (
            f"#{headcount} vakansiya\n\n"
            f"Vakansiya nomi: {title}\n"
            f"Necha kishi kerak: {headcount}\n"
            f"Lokatsiya (yozma): {loc_text}\n"
            f"Ish vaqti: {work_time}\n"
            f"Xizmati haqi: {salary}\n"
            f"Bron qilish narxi: {deposit} so'm\n"
        )

        temp_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Ishni bron qilish",
                        url=f"https://t.me/{self.bot_username}?start=book_0",
                    )
                ]
            ]
        )

        msg: Message = await context.bot.send_message(
            chat_id=self.channel_id,
            text=caption,
            reply_markup=temp_keyboard,
            parse_mode=ParseMode.HTML,
        )

        if latitude is not None and longitude is not None:
            await context.bot.send_location(
                chat_id=self.channel_id,
                latitude=latitude,
                longitude=longitude,
            )

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

        real_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Ishni bron qilish",
                        url=f"https://t.me/{self.bot_username}?start=book_{vac_id}",
                    )
                ]
            ]
        )
        await msg.edit_reply_markup(reply_markup=real_keyboard)

        await update.message.reply_text(f"Vakansiya kanalga yuborildi (ID: {vac_id}).")
        return ConversationHandler.END

    # -------------------- REGISTRATION --------------------

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

    offer_text = (
        "⬇ Ommaviy oferta ⬇\n\n"
        "Botdan foydalanish orqali siz shartlarga rozilik bildirasiz.\n"
        "Rozi bo'lsangiz 'Roziman' tugmasini bosing."
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Roziman", callback_data="accept_offer"),
                InlineKeyboardButton("Rad etaman", callback_data="decline_offer"),
            ]
        ]
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
        user_id = update.effective_user.id
        pending_vacancy_id = context.user_data.get("pending_vacancy_id")

        upsert_user(
            self.db_path,
            user_id=user_id,
            name=context.user_data.get("reg_name"),
            phone=context.user_data.get("reg_phone"),
            offer_accepted=True,
            passport_file_id=file_id,
            approved=False,
            pending_vacancy_id=pending_vacancy_id,
        )

        text = (
            f"#{user_id} yangi foydalanuvchi:\n"
            f"Ismi: {context.user_data.get('reg_name')}\n"
            f"Telefon raqami: {context.user_data.get('reg_phone')}\n"
            f"Ommaviy ofertaga rozilik: rozi ✅\n"
            f"Passport surati: quyida\n"
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Tasdiqlayman", callback_data=f"approve_reg:{user_id}"),
                    InlineKeyboardButton("Tasdiqlamayman", callback_data=f"reject_reg:{user_id}"),
                ]
            ]
        )

        for admin_id in self.admin_ids:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=file_id)
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            except Exception as exc:
                logger.warning(f"Failed to notify admin {admin_id}: {exc}")

        await update.message.reply_text(
            "Ma'lumotlaringiz yuborildi. Admin tasdig'ini kuting.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    # -------------------- ADMIN APPROVAL --------------------

    async def admin_approve_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        try:
            user_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        upsert_user(self.db_path, user_id=user_id, approved=True)
        await self.send_welcome_and_continue(user_id=user_id, context=context)
        await query.message.edit_text("Foydalanuvchi tasdiqlandi.")

    async def admin_reject_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        try:
            user_id = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        context.chat_data["pending_rejection_user"] = user_id
        user = get_user(self.db_path, user_id)
        user_name = user["name"] if user and user.get("name") else "foydalanuvchi"

        await query.message.reply_text(
            f"{user_name} Boss, nega bu foydalanuvchini tasdiqlamadingiz? Dal*ayobmisiz?\n\nSababni yozing:"
        )
        try:
            await query.message.delete()
        except Exception:
            pass

    # -------------------- TEXT / PHOTO GLOBAL --------------------

    async def generic_text_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if user_id in self.admin_ids and "pending_rejection_user" in context.chat_data:
            rejected_user_id = context.chat_data.pop("pending_rejection_user")
            reason = update.message.text.strip()

            try:
                await context.bot.send_message(
                    chat_id=rejected_user_id,
                    text=(
                        f"Kechirasiz, {get_user(self.db_path, rejected_user_id)['name']} siz "
                        f"“Kunlik vakansiya” shartlaridan o‘ta olmaganingiz uchun hozircha "
                        f"sizning arizangizni qabul qila olmaymiz.\n\n"
                        f"Sabab:\n{reason}"
                    ),
                )
            except Exception as exc:
                logger.warning(f"Failed to send rejection reason: {exc}")

            await update.message.reply_text("Rad etish sababi foydalanuvchiga yuborildi.")
            return

    async def global_photo_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.effective_user:
            return

        user_id = update.effective_user.id

        if user_id not in self.pending_bookings:
            return

        booking_id = self.pending_bookings[user_id]
        receipt_file_id = update.message.photo[-1].file_id
        set_booking_receipt(self.db_path, booking_id, receipt_file_id)

        booking = get_booking(self.db_path, booking_id)
        if not booking:
            await update.message.reply_text("Booking topilmadi.")
            return

        vacancy_id = booking["vacancy_id"]

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Tasdiqlayman",
                        callback_data=f"confirm_receipt:{vacancy_id}:{booking_id}",
                    ),
                    InlineKeyboardButton(
                        "Tasdiqlamayman",
                        callback_data=f"decline_receipt:{vacancy_id}:{booking_id}",
                    ),
                ]
            ]
        )

        text = (
            f"To'lov cheki\n"
            f"Foydalanuvchi ID: {user_id}\n"
            f"Booking ID: {booking_id}\n"
            f"Vakansiya ID: {vacancy_id}\n\n"
            f"Tasdiqlaysizmi?"
        )

        for admin_id in self.admin_ids:
            try:
                await context.bot.send_photo(chat_id=admin_id, photo=receipt_file_id)
                await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=keyboard)
            except Exception as exc:
                logger.warning(f"Failed to notify admin {admin_id} about receipt: {exc}")

        self.pending_bookings.pop(user_id, None)
        await update.message.reply_text("Chek yuborildi. Admin tasdig'ini kuting.")

    # -------------------- RECEIPT ADMIN --------------------

    async def admin_confirm_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            vacancy_id = int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        set_booking_confirmed(self.db_path, booking_id, True)
        decrement_vacancy_remaining(self.db_path, vacancy_id)

        booking = get_booking(self.db_path, booking_id)
        if booking:
            try:
                await context.bot.send_message(
                    chat_id=booking["user_id"],
                    text="Bron to'lovi tasdiqlandi. Ish siz uchun bron qilindi!",
                )
            except Exception as exc:
                logger.warning(f"Failed to notify user: {exc}")

        await self.send_vacancy_remaining_notice(vacancy_id, context)
        await query.message.edit_text("To'lov tasdiqlandi.")

    async def admin_decline_receipt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        try:
            _, vacancy_id_str, booking_id_str = query.data.split(":")
            _ = int(vacancy_id_str)
            booking_id = int(booking_id_str)
        except (ValueError, IndexError):
            await query.message.reply_text("Noto'g'ri ma'lumot.")
            return

        booking = get_booking(self.db_path, booking_id)
        if booking:
            self.pending_bookings[booking["user_id"]] = booking_id
            try:
                await context.bot.send_message(
                    chat_id=booking["user_id"],
                    text="Kechirasiz, chek tasdiqlanmadi. Iltimos, qaytadan to'g'ri chek yuboring.",
                )
            except Exception as exc:
                logger.warning(f"Failed to notify user: {exc}")

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
