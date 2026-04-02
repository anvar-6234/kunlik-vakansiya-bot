"""
Kunlik Vakansiya Telegram Bot — Production-Level
==================================================
Telegram kanal orqali kunlik vakansiyalar e'lon qilish,
foydalanuvchilarni ro'yxatdan o'tkazish, admin tasdiqlash,
to'lov tekshiruvi va bron qilish tizimi.

Stack: Python 3.12 + python-telegram-bot 20.7 + SQLite
"""

import os
import logging
import sqlite3
import html
from datetime import datetime, timezone
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "").lstrip("@")
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]
CHANNEL_ID: int = int(os.environ.get("CHANNEL_ID", "0"))
CARD_NUMBER: str = os.environ.get("CARD_NUMBER", "4916990356074515")
CARD_HOLDER: str = os.environ.get("CARD_HOLDER", "Anvarxon Mansurov")
DATA_DIR: str = os.environ.get("DATA_DIR", "data")
CHANNEL_PUBLIC_USERNAME: str = os.environ.get("CHANNEL_PUBLIC_USERNAME", "")

Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.db")

# ──────────────────────────────────────────────
#  CONVERSATION STATES
# ──────────────────────────────────────────────
# Registration
REG_NAME, REG_PHONE, REG_OFFERTA, REG_PASSPORT = range(4)

# Vacancy creation
(
    VAC_TITLE, VAC_HEADCOUNT, VAC_DATE, VAC_LOCATION,
    VAC_WORKTIME, VAC_SALARY, VAC_DEPOSIT, VAC_GEO,
    VAC_PREVIEW, VAC_EDIT_CHOOSE, VAC_EDIT_VALUE,
) = range(10, 21)

# Admin rejection reason
ADM_REJECT_REASON = 30

# Admin messaging
MSG_CHOOSE_TARGET, MSG_CHOOSE_VACANCY, MSG_CHOOSE_USER, MSG_TEXT = range(40, 44)

# ──────────────────────────────────────────────
#  OFFERTA TEXT
# ──────────────────────────────────────────────
OFFERTA_TEXT = """📝 Foydalanuvchi ofertasi

"Kunlik vakansiya" markazi tomonidan taqdim etiladigan xizmatlar va undan foydalanuvchilar orasidagi shartnoma:

1. Umumiy qoidalar.

Ushbu botdan foydalanish orqali siz quyidagi shartlarga rozilik bildirasiz.

Bizning xizmat - kunlik ishlarga nomzodlarni ish beruvchilar bilan aloqasini o'rnatish.

2. Xizmat haqi.

Har bir ish e'lonida xizmat haqi miqdori alohida ko'rsatiladi.

Nomzod ishga yozilishdan oldin ko'rsatilgan summani to'laydi va to'lov tasdig'ini (check) botga yuboradi. Qalbaki check yuborish qat'iyan taqiqlanadi, bunday holat aniqlansa u foydalanuvchiga qaytib ish berilmaydi.

3. Majburiyatlar.

To'lovdan so'ng nomzod ishga chiqishi shart. Sababsiz chiqmaslik xizmatdan chetlashtirishga olib keladi.

Biz ish beruvchi va nomzod o'rtasidagi nizolarga hech bir ko'rinishda javobgar emasmiz, lekin imkon qadar yordam beramiz.

4. Javobgarlik chegarasi.

Ish haqi, ish joyi sharoiti va boshqa qo'shimcha kelishuvlar uchun faqat ish beruvchi javobgar.

Bizning yagona vazifamiz - faqat kontakt bog'lash va e'lonlarni yetkazish xolos.

5. Yakuniy shartlar.

Oferta va qoidalar doimiy yangilanib boradi. Botdan foydalanish orqali siz ushbu shartlarga rozilik bildirgan bo'lasiz."""


# ══════════════════════════════════════════════
#  DATABASE LAYER
# ══════════════════════════════════════════════

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = _get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            name        TEXT,
            phone       TEXT,
            username    TEXT,
            offer_accepted INTEGER DEFAULT 0,
            passport_file_id TEXT,
            approved    INTEGER DEFAULT 0,
            created_at  TEXT,
            updated_at  TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            vacancy_id      INTEGER,
            name            TEXT,
            phone           TEXT,
            username        TEXT,
            offer_accepted  INTEGER DEFAULT 0,
            passport_file_id TEXT,
            status          TEXT DEFAULT 'pending',
            rejection_reason TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS vacancies (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT NOT NULL,
            headcount           INTEGER NOT NULL,
            date_text           TEXT,
            location_text       TEXT,
            work_time           TEXT,
            salary              TEXT,
            deposit             TEXT,
            latitude            REAL,
            longitude           REAL,
            channel_message_id  INTEGER,
            remaining           INTEGER,
            created_at          TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id      INTEGER NOT NULL,
            user_id         INTEGER NOT NULL,
            receipt_file_id TEXT,
            confirmed       INTEGER DEFAULT 0,
            created_at      TEXT
        )
    """)

    # ── migrations ──
    _migrate(conn)

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing columns if upgrading from older schema."""
    cursor = conn.cursor()
    tables_cols: dict[str, list[tuple[str, str]]] = {
        "users": [("updated_at", "TEXT")],
        "applications": [("updated_at", "TEXT"), ("vacancy_id", "INTEGER")],
    }
    for table, cols in tables_cols.items():
        existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        for col_name, col_type in cols:
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                logger.info("Migrated: added %s.%s", table, col_name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── user helpers ──

def db_get_user(user_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def db_upsert_user(user_id: int, **kwargs) -> None:
    conn = _get_conn()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    now = _now()
    if existing:
        kwargs["updated_at"] = now
        sets = ", ".join(f"{k}=?" for k in kwargs)
        conn.execute(f"UPDATE users SET {sets} WHERE user_id=?", (*kwargs.values(), user_id))
    else:
        kwargs["user_id"] = user_id
        kwargs["created_at"] = now
        kwargs["updated_at"] = now
        cols = ", ".join(kwargs.keys())
        phs = ", ".join("?" for _ in kwargs)
        conn.execute(f"INSERT INTO users ({cols}) VALUES ({phs})", tuple(kwargs.values()))
    conn.commit()
    conn.close()


def db_get_all_users(limit: int = 200, offset: int = 0) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_count_users() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
    conn.close()
    return row["cnt"]


# ── application helpers ──

def db_create_application(user_id: int, vacancy_id: int | None, name: str,
                          phone: str, username: str | None,
                          offer_accepted: int, passport_file_id: str) -> int:
    conn = _get_conn()
    now = _now()
    cur = conn.execute(
        """INSERT INTO applications
           (user_id, vacancy_id, name, phone, username, offer_accepted, passport_file_id, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,'pending',?,?)""",
        (user_id, vacancy_id, name, phone, username, offer_accepted, passport_file_id, now, now),
    )
    app_id = cur.lastrowid
    conn.commit()
    conn.close()
    return app_id


def db_update_application(app_id: int, **kwargs) -> None:
    conn = _get_conn()
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE applications SET {sets} WHERE id=?", (*kwargs.values(), app_id))
    conn.commit()
    conn.close()


def db_get_application(app_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── vacancy helpers ──

def db_create_vacancy(title: str, headcount: int, date_text: str,
                      location_text: str, work_time: str, salary: str,
                      deposit: str, latitude: float | None,
                      longitude: float | None) -> int:
    conn = _get_conn()
    now = _now()
    cur = conn.execute(
        """INSERT INTO vacancies
           (title, headcount, date_text, location_text, work_time, salary, deposit,
            latitude, longitude, remaining, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (title, headcount, date_text, location_text, work_time, salary, deposit,
         latitude, longitude, headcount, now),
    )
    vid = cur.lastrowid
    conn.commit()
    conn.close()
    return vid


def db_get_vacancy(vid: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM vacancies WHERE id=?", (vid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def db_update_vacancy(vid: int, **kwargs) -> None:
    conn = _get_conn()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE vacancies SET {sets} WHERE id=?", (*kwargs.values(), vid))
    conn.commit()
    conn.close()


def db_get_all_vacancies() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM vacancies ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def db_decrement_remaining(vid: int) -> dict:
    conn = _get_conn()
    conn.execute("UPDATE vacancies SET remaining = remaining - 1 WHERE id=? AND remaining > 0", (vid,))
    conn.commit()
    row = conn.execute("SELECT * FROM vacancies WHERE id=?", (vid,)).fetchone()
    conn.close()
    return dict(row)


# ── booking helpers ──

def db_create_booking(vacancy_id: int, user_id: int) -> int:
    conn = _get_conn()
    now = _now()
    cur = conn.execute(
        "INSERT INTO bookings (vacancy_id, user_id, confirmed, created_at) VALUES (?,?,0,?)",
        (vacancy_id, user_id, now),
    )
    bid = cur.lastrowid
    conn.commit()
    conn.close()
    return bid


def db_get_booking(bid: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def db_update_booking(bid: int, **kwargs) -> None:
    conn = _get_conn()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    conn.execute(f"UPDATE bookings SET {sets} WHERE id=?", (*kwargs.values(), bid))
    conn.commit()
    conn.close()


def db_get_user_pending_booking(user_id: int, vacancy_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bookings WHERE user_id=? AND vacancy_id=? AND confirmed=0 ORDER BY id DESC LIMIT 1",
        (user_id, vacancy_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def db_get_user_confirmed_booking(user_id: int, vacancy_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM bookings WHERE user_id=? AND vacancy_id=? AND confirmed=1 LIMIT 1",
        (user_id, vacancy_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def db_get_bookings_for_vacancy(vacancy_id: int, confirmed: int | None = None) -> list[dict]:
    conn = _get_conn()
    if confirmed is not None:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE vacancy_id=? AND confirmed=?",
            (vacancy_id, confirmed),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE vacancy_id=?",
            (vacancy_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════
#  UTILITY HELPERS
# ══════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📝 Yangi vakansiya qo'shish"],
            ["📊 Statistika"],
            ["✉️ Foydalanuvchiga yozish"],
        ],
        resize_keyboard=True,
    )


def vacancy_text(v: dict) -> str:
    return (
        f"#{v['id']} vakansiya\n\n"
        f"Vakansiya nomi: {v['title']}\n"
        f"Necha kishi kerak: {v['headcount']}\n"
        f"Sana: {v['date_text']}\n"
        f"Lokatsiya (yozma): {v['location_text']}\n"
        f"Ish vaqti: {v['work_time']}\n"
        f"Xizmati haqi: {v['salary']}\n"
        f"Bron qilish narxi: {v['deposit']} so'm"
    )


def vacancy_link(v: dict) -> str:
    """Return a clickable link to the channel post if possible."""
    if CHANNEL_PUBLIC_USERNAME and v.get("channel_message_id"):
        return f"https://t.me/{CHANNEL_PUBLIC_USERNAME}/{v['channel_message_id']}"
    return ""


def booking_inline_button(vacancy_id: int) -> InlineKeyboardMarkup:
    url = f"https://t.me/{BOT_USERNAME}?start=book_{vacancy_id}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("📋 Ishni bron qilish", url=url)]])


def user_mention(u: dict) -> str:
    name = u.get("name") or "Foydalanuvchi"
    uid = u["user_id"]
    return f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'


def user_info_text(u: dict) -> str:
    parts = [f"👤 {user_mention(u)}"]
    if u.get("phone"):
        parts.append(f"📱 {u['phone']}")
    if u.get("username"):
        parts.append(f"🔗 @{u['username']}")
    return "\n".join(parts)


# ══════════════════════════════════════════════
#  /START  &  DEEP LINK
# ══════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    args = context.args or []

    # ── admin start ──
    if is_admin(user.id):
        await update.message.reply_text(
            f"Salom, Admin {user.first_name}! 👋\nQuyidagi menyudan foydalaning:",
            reply_markup=admin_reply_keyboard(),
        )
        # If admin also clicked a deep link, handle it
        if args and args[0].startswith("book_"):
            pass  # admins don't book
        return ConversationHandler.END

    # ── deep link: book_<vacancy_id> ──
    vacancy_id = None
    if args and args[0].startswith("book_"):
        try:
            vacancy_id = int(args[0].removeprefix("book_"))
        except ValueError:
            vacancy_id = None

    db_user = db_get_user(user.id)

    # Approved user → skip registration
    if db_user and db_user.get("approved"):
        if vacancy_id:
            await _start_payment_flow(update, context, user.id, vacancy_id)
        else:
            await update.message.reply_text(
                "Salom! Siz allaqachon ro'yxatdan o'tgansiz ✅\n"
                "Kanaldagi vakansiyalardan birini tanlang va \"Ishni bron qilish\" tugmasini bosing."
            )
        return ConversationHandler.END

    # Not approved → start registration
    db_upsert_user(user.id, username=user.username)
    context.user_data["reg_vacancy_id"] = vacancy_id

    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Kunlik vakansiya botiga xush kelibsiz!\n"
        "Ro'yxatdan o'tish uchun quyidagi ma'lumotlarni kiriting.\n\n"
        "📝 Ism va familiyangizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REG_NAME


# ══════════════════════════════════════════════
#  REGISTRATION FLOW
# ══════════════════════════════════════════════

async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if len(text) < 2:
        await update.message.reply_text("⚠️ Iltimos, to'liq ism va familiyangizni kiriting:")
        return REG_NAME
    context.user_data["reg_name"] = text
    keyboard = [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]]
    await update.message.reply_text(
        "📱 Telefon raqamingizni yuboring.\n"
        "Quyidagi tugmani bosing yoki qo'lda kiriting (masalan: +998901234567):",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )
    return REG_PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
    if len(phone) < 9:
        await update.message.reply_text("⚠️ Iltimos, to'g'ri telefon raqam kiriting:")
        return REG_PHONE
    context.user_data["reg_phone"] = phone

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Roziman ✅", callback_data="offerta_yes")],
        [InlineKeyboardButton("Rad etaman ❌", callback_data="offerta_no")],
    ])
    await update.message.reply_text(OFFERTA_TEXT, reply_markup=keyboard)
    return REG_OFFERTA


async def reg_offerta_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "offerta_no":
        await query.edit_message_text(
            "❌ Siz ofertani rad etdingiz. Ro'yxatdan o'tish bekor qilindi.\n"
            "Qayta urinish uchun /start buyrug'ini yuboring."
        )
        return ConversationHandler.END

    context.user_data["reg_offerta"] = True
    await query.edit_message_text("✅ Oferta qabul qilindi!")
    await query.message.reply_text(
        "📸 Endi passport rasmingizni yuboring:\n"
        "(Passportning birinchi sahifasi rasmini yuboring)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REG_PASSPORT


async def reg_passport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("⚠️ Iltimos, passport rasmini yuboring (rasm sifatida):")
        return REG_PASSPORT

    user = update.effective_user
    photo = update.message.photo[-1]
    file_id = photo.file_id

    name = context.user_data.get("reg_name", "")
    phone = context.user_data.get("reg_phone", "")
    vacancy_id = context.user_data.get("reg_vacancy_id")

    # Save user
    db_upsert_user(
        user.id,
        name=name,
        phone=phone,
        username=user.username,
        offer_accepted=1,
        passport_file_id=file_id,
    )

    # Create application
    app_id = db_create_application(
        user_id=user.id,
        vacancy_id=vacancy_id,
        name=name,
        phone=phone,
        username=user.username,
        offer_accepted=1,
        passport_file_id=file_id,
    )

    await update.message.reply_text(
        "✅ Ma'lumotlaringiz qabul qilindi!\n"
        "⏳ Admin tekshiruvidan o'tishingizni kuting. Tez orada javob beramiz!"
    )

    # ── notify admins ──
    vac_text = ""
    if vacancy_id:
        v = db_get_vacancy(vacancy_id)
        if v:
            vac_text = f"Vakansiya: #{v['id']} — {v['title']}\n"

    caption = (
        f"#APP{app_id} yangi foydalanuvchi arizasi:\n\n"
        f"{vac_text}"
        f"Ismi: {name}\n"
        f"Telefon raqami: {phone}\n"
        f"Ommaviy ofertaga rozilik: rozi ✅\n"
        f"Passport surati: quyida"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Tasdiqlayman ✅", callback_data=f"app_approve_{app_id}")],
        [InlineKeyboardButton("Tasdiqlamayman ❌", callback_data=f"app_reject_{app_id}")],
    ])

    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=aid, photo=file_id, caption=caption, reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("Failed to notify admin %s: %s", aid, e)

    return ConversationHandler.END


# ══════════════════════════════════════════════
#  APPLICATION APPROVAL / REJECTION
# ══════════════════════════════════════════════

async def app_approve_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    app_id = int(query.data.removeprefix("app_approve_"))
    app = db_get_application(app_id)
    if not app:
        await query.edit_message_caption("⚠️ Ariza topilmadi.")
        return
    if app["status"] != "pending":
        await query.edit_message_caption(query.message.caption + f"\n\nℹ️ Bu ariza allaqachon: {app['status']}")
        return

    db_update_application(app_id, status="approved")
    db_upsert_user(app["user_id"], approved=1)

    await query.edit_message_caption(query.message.caption + "\n\n✅ TASDIQLANDI")

    name = app["name"] or "foydalanuvchi"
    try:
        await context.bot.send_message(
            chat_id=app["user_id"],
            text=(
                f"Hurmatli {name} sizni Kunlik vakansiya jamoasiga "
                f"qabul qilinganingiz bilan tabriklayman.\n\n"
                f"Quyida ish bilan batafsil tanishishingiz va bron qilishingiz mumkin!"
            ),
        )
    except Exception as e:
        logger.error("Failed to send welcome to %s: %s", app["user_id"], e)

    # If application was for a specific vacancy, start payment flow
    if app.get("vacancy_id"):
        v = db_get_vacancy(app["vacancy_id"])
        if v and v["remaining"] > 0:
            await _send_payment_info(context, app["user_id"], v)


async def app_reject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    app_id = int(query.data.removeprefix("app_reject_"))
    app = db_get_application(app_id)
    if not app:
        await query.edit_message_caption("⚠️ Ariza topilmadi.")
        return
    if app["status"] != "pending":
        await query.edit_message_caption(query.message.caption + f"\n\nℹ️ Bu ariza allaqachon: {app['status']}")
        return

    await query.edit_message_caption(query.message.caption + "\n\n❌ RAD ETILDI (sabab kutilmoqda...)")

    context.user_data["reject_app_id"] = app_id
    context.user_data["reject_app_name"] = app.get("name", "foydalanuvchi")
    context.user_data["reject_app_user_id"] = app["user_id"]

    await query.message.reply_text(
        f"Sababni yozing — nima uchun {app.get('name', '')} ni tasdiqlamadingiz?"
    )
    context.user_data["awaiting_reject_reason"] = True


async def _handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called from the general text handler when admin is typing rejection reason."""
    reason = update.message.text.strip()
    app_id = context.user_data.pop("reject_app_id", None)
    app_name = context.user_data.pop("reject_app_name", "foydalanuvchi")
    app_user_id = context.user_data.pop("reject_app_user_id", None)
    context.user_data.pop("awaiting_reject_reason", None)

    if not app_id or not app_user_id:
        return

    db_update_application(app_id, status="rejected", rejection_reason=reason)

    await update.message.reply_text(f"✅ Sabab saqlandi va foydalanuvchiga yuborildi.")

    try:
        await context.bot.send_message(
            chat_id=app_user_id,
            text=(
                f"Kechirasiz, {app_name} siz \"Kunlik vakansiya\" shartlaridan "
                f"o'ta olmaganingiz uchun hozircha sizning arizangizni qabul qila olmaymiz.\n\n"
                f"Sabab:\n{reason}"
            ),
        )
    except Exception as e:
        logger.error("Failed to send rejection to %s: %s", app_user_id, e)


# ══════════════════════════════════════════════
#  PAYMENT FLOW
# ══════════════════════════════════════════════

async def _start_payment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              user_id: int, vacancy_id: int) -> None:
    """Entry point for approved user clicking a vacancy deep link."""
    v = db_get_vacancy(vacancy_id)
    if not v:
        await update.message.reply_text("⚠️ Vakansiya topilmadi.")
        return
    if v["remaining"] <= 0:
        await update.message.reply_text("❌ Bu vakansiyada bo'sh joy qolmagan.")
        return

    # Check if already confirmed
    existing = db_get_user_confirmed_booking(user_id, vacancy_id)
    if existing:
        await update.message.reply_text("✅ Siz bu vakansiyani allaqachon bron qilgansiz!")
        return

    # Check if pending booking exists
    pending = db_get_user_pending_booking(user_id, vacancy_id)
    if pending and pending.get("receipt_file_id"):
        await update.message.reply_text("⏳ Sizning chekingiz tekshirilmoqda. Kuting.")
        return

    await _send_payment_info(context, user_id, v)
    context.user_data["payment_vacancy_id"] = vacancy_id


async def _send_payment_info(context: ContextTypes.DEFAULT_TYPE,
                             user_id: int, v: dict) -> None:
    """Send vacancy details + card info to user."""
    text = (
        f"{vacancy_text(v)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Ishni bron qilishingiz uchun quyidagi kartaga bron summasini o'tkazing:\n\n"
        f"💳 {CARD_NUMBER}\n"
        f"👤 {CARD_HOLDER}\n"
        f"💰 Summa: {v['deposit']} so'm\n\n"
        f"Chekni shu botga rasm qilib yuboring."
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except Exception as e:
        logger.error("Failed to send payment info to %s: %s", user_id, e)


# ══════════════════════════════════════════════
#  PHOTO HANDLER  (passport during reg is handled by ConversationHandler)
#  This handles RECEIPT photos from approved users
# ══════════════════════════════════════════════

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_admin(user.id):
        return  # admins don't send receipts

    db_user = db_get_user(user.id)
    if not db_user or not db_user.get("approved"):
        return  # not approved, ignore

    photo = update.message.photo[-1]
    file_id = photo.file_id

    vacancy_id = context.user_data.get("payment_vacancy_id")
    if not vacancy_id:
        await update.message.reply_text(
            "⚠️ Qaysi vakansiya uchun chek yuborayotganingiz aniqlanmadi.\n"
            "Kanaldagi vakansiyadan \"Ishni bron qilish\" tugmasini bosing."
        )
        return

    v = db_get_vacancy(vacancy_id)
    if not v:
        await update.message.reply_text("⚠️ Vakansiya topilmadi.")
        return
    if v["remaining"] <= 0:
        await update.message.reply_text("❌ Bu vakansiyada bo'sh joy qolmagan.")
        return

    # Check duplicate
    existing_confirmed = db_get_user_confirmed_booking(user.id, vacancy_id)
    if existing_confirmed:
        await update.message.reply_text("✅ Siz bu vakansiyani allaqachon bron qilgansiz!")
        return

    # Create or update booking
    pending = db_get_user_pending_booking(user.id, vacancy_id)
    if pending:
        db_update_booking(pending["id"], receipt_file_id=file_id)
        bid = pending["id"]
    else:
        bid = db_create_booking(vacancy_id, user.id)
        db_update_booking(bid, receipt_file_id=file_id)

    await update.message.reply_text("✅ Chekingiz qabul qilindi! Admin tekshiruvini kuting.")

    # Notify admins
    caption = (
        f"💳 To'lov cheki\n\n"
        f"Foydalanuvchi: {db_user.get('name', '—')}\n"
        f"Telefon: {db_user.get('phone', '—')}\n"
        f"Vakansiya: #{v['id']} — {v['title']}\n"
        f"Booking ID: #{bid}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Tasdiqlayman ✅", callback_data=f"pay_approve_{bid}")],
        [InlineKeyboardButton("Tasdiqlamayman ❌", callback_data=f"pay_reject_{bid}")],
    ])
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=aid, photo=file_id, caption=caption, reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("Failed to send receipt to admin %s: %s", aid, e)


# ══════════════════════════════════════════════
#  PAYMENT APPROVAL / REJECTION
# ══════════════════════════════════════════════

async def pay_approve_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    bid = int(query.data.removeprefix("pay_approve_"))
    booking = db_get_booking(bid)
    if not booking:
        await query.edit_message_caption("⚠️ Booking topilmadi.")
        return
    if booking["confirmed"]:
        await query.edit_message_caption(query.message.caption + "\n\nℹ️ Allaqachon tasdiqlangan.")
        return

    db_update_booking(bid, confirmed=1)
    v = db_decrement_remaining(booking["vacancy_id"])
    db_user = db_get_user(booking["user_id"])

    await query.edit_message_caption(query.message.caption + "\n\n✅ TO'LOV TASDIQLANDI")

    # ── notify user ──
    name = db_user.get("name", "foydalanuvchi") if db_user else "foydalanuvchi"
    vac_link = vacancy_link(v)
    if vac_link:
        vac_ref = f'<a href="{vac_link}">{html.escape(v["title"])}</a>'
    else:
        vac_ref = v["title"]

    msg_text = (
        f"Hurmatli {html.escape(name)}, siz {vac_ref} ishi uchun joy bron qildingiz.\n\n"
        f"Pastda qo'shimcha tafsilotlar va lokatsiya yuboryapman, "
        f"iltimos aytilgan joyga kechikmay keling!"
    )
    try:
        await context.bot.send_message(
            chat_id=booking["user_id"], text=msg_text, parse_mode=ParseMode.HTML,
        )
        # Send vacancy details
        await context.bot.send_message(
            chat_id=booking["user_id"], text=vacancy_text(v),
        )
        # Send location if available
        if v.get("latitude") and v.get("longitude"):
            await context.bot.send_location(
                chat_id=booking["user_id"],
                latitude=v["latitude"],
                longitude=v["longitude"],
            )
    except Exception as e:
        logger.error("Failed to notify user %s: %s", booking["user_id"], e)

    # ── update channel ──
    remaining = v["remaining"]
    if remaining > 0:
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"🔥 Vakansiya uchun {remaining}/{v['headcount']} bo'sh joy qoldi, shoshiling!",
                reply_to_message_id=v.get("channel_message_id"),
                reply_markup=booking_inline_button(v["id"]),
            )
        except Exception as e:
            logger.error("Failed to reply to channel: %s", e)
    else:
        # Close vacancy
        try:
            closed_text = vacancy_text(v) + "\n\n❌ Ish joylari qolmadi! Rahmat )"
            await context.bot.edit_message_text(
                chat_id=CHANNEL_ID,
                message_id=v["channel_message_id"],
                text=closed_text,
            )
        except Exception as e:
            logger.error("Failed to edit channel post: %s", e)


async def pay_reject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    bid = int(query.data.removeprefix("pay_reject_"))
    booking = db_get_booking(bid)
    if not booking:
        await query.edit_message_caption("⚠️ Booking topilmadi.")
        return

    db_update_booking(bid, receipt_file_id=None)  # allow re-upload

    await query.edit_message_caption(query.message.caption + "\n\n❌ TO'LOV RAD ETILDI")

    db_user = db_get_user(booking["user_id"])
    v = db_get_vacancy(booking["vacancy_id"])
    name = db_user.get("name", "foydalanuvchi") if db_user else "foydalanuvchi"
    try:
        await context.bot.send_message(
            chat_id=booking["user_id"],
            text=(
                f"❌ Kechirasiz, {name}!\n\n"
                f"Sizning to'lovingiz tasdiqlanmadi.\n"
                f"Vakansiya: #{v['id']} — {v['title']}\n\n"
                f"Iltimos, to'g'ri chek rasmini qayta yuboring."
            ),
        )
    except Exception as e:
        logger.error("Failed to notify user %s: %s", booking["user_id"], e)


# ══════════════════════════════════════════════
#  VACANCY CREATION FLOW
# ══════════════════════════════════════════════

async def vac_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data["vac"] = {}
    await update.message.reply_text(
        "📝 Yangi vakansiya yaratish\n\nVakansiya nomini kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return VAC_TITLE


async def vac_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["title"] = update.message.text.strip()
    await update.message.reply_text("👥 Necha kishi kerak? (raqam kiriting)")
    return VAC_HEADCOUNT


async def vac_headcount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        n = int(update.message.text.strip())
        if n < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Iltimos, to'g'ri musbat raqam kiriting:")
        return VAC_HEADCOUNT
    context.user_data["vac"]["headcount"] = n
    await update.message.reply_text("📅 Sanani kiriting (masalan: 2025-04-05 yoki 5-aprel):")
    return VAC_DATE


async def vac_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["date_text"] = update.message.text.strip()
    await update.message.reply_text("📍 Lokatsiyani yozing (manzil):")
    return VAC_LOCATION


async def vac_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["location_text"] = update.message.text.strip()
    await update.message.reply_text("🕐 Ish vaqtini kiriting (masalan: 09:00 - 18:00):")
    return VAC_WORKTIME


async def vac_worktime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["work_time"] = update.message.text.strip()
    await update.message.reply_text("💰 Xizmati haqini kiriting (masalan: 148,000 so'm):")
    return VAC_SALARY


async def vac_salary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["salary"] = update.message.text.strip()
    await update.message.reply_text("💳 Bron qilish narxini kiriting (masalan: 15,000):")
    return VAC_DEPOSIT


async def vac_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vac"]["deposit"] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="geo_skip")],
    ])
    await update.message.reply_text(
        "📍 Geolokatsiya yuboring (lokatsiyani forward qilishingiz ham mumkin)\n"
        "yoki \"O'tkazib yuborish\" tugmasini bosing.",
        reply_markup=keyboard,
    )
    return VAC_GEO


async def vac_geo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    if loc:
        context.user_data["vac"]["latitude"] = loc.latitude
        context.user_data["vac"]["longitude"] = loc.longitude
    elif update.message.venue:
        context.user_data["vac"]["latitude"] = update.message.venue.location.latitude
        context.user_data["vac"]["longitude"] = update.message.venue.location.longitude
    return await _show_preview(update, context)


async def vac_geo_skip_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["vac"]["latitude"] = None
    context.user_data["vac"]["longitude"] = None
    await query.edit_message_text("📍 Geolokatsiya o'tkazib yuborildi.")
    return await _show_preview(update, context)


async def _show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = context.user_data["vac"]
    geo_line = ""
    if d.get("latitude"):
        geo_line = "\n📍 Geolokatsiya qo'shilgan"

    preview = (
        f"📋 PREVIEW\n\n"
        f"Vakansiya nomi: {d['title']}\n"
        f"Necha kishi kerak: {d['headcount']}\n"
        f"Sana: {d['date_text']}\n"
        f"Lokatsiya (yozma): {d['location_text']}\n"
        f"Ish vaqti: {d['work_time']}\n"
        f"Xizmati haqi: {d['salary']}\n"
        f"Bron qilish narxi: {d['deposit']} so'm"
        f"{geo_line}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Tasdiqlayman ✅", callback_data="vac_confirm")],
        [InlineKeyboardButton("Tasdiqlamayman ❌", callback_data="vac_edit")],
    ])
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(preview, reply_markup=keyboard)
    return VAC_PREVIEW


async def vac_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data.get("vac", {})

    vid = db_create_vacancy(
        title=d["title"],
        headcount=d["headcount"],
        date_text=d["date_text"],
        location_text=d["location_text"],
        work_time=d["work_time"],
        salary=d["salary"],
        deposit=d["deposit"],
        latitude=d.get("latitude"),
        longitude=d.get("longitude"),
    )
    v = db_get_vacancy(vid)
    post = vacancy_text(v)
    kb = booking_inline_button(vid)

    try:
        sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=post, reply_markup=kb)
        db_update_vacancy(vid, channel_message_id=sent.message_id)

        if d.get("latitude") and d.get("longitude"):
            await context.bot.send_location(
                chat_id=CHANNEL_ID, latitude=d["latitude"], longitude=d["longitude"],
            )

        await query.edit_message_text(f"✅ #{vid} vakansiya kanalga yuborildi!")
    except Exception as e:
        logger.error("Failed to post vacancy: %s", e)
        await query.edit_message_text(f"⚠️ Vakansiya yaratildi (#{vid}), lekin kanalga yuborishda xatolik: {e}")

    context.user_data.pop("vac", None)
    await query.message.reply_text("Menyuga qaytish:", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END


async def vac_edit_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Vakansiya nomi", callback_data="vedit_title")],
        [InlineKeyboardButton("Necha kishi", callback_data="vedit_headcount")],
        [InlineKeyboardButton("Sana", callback_data="vedit_date_text")],
        [InlineKeyboardButton("Lokatsiya", callback_data="vedit_location_text")],
        [InlineKeyboardButton("Ish vaqti", callback_data="vedit_work_time")],
        [InlineKeyboardButton("Xizmati haqi", callback_data="vedit_salary")],
        [InlineKeyboardButton("Bron narxi", callback_data="vedit_deposit")],
        [InlineKeyboardButton("Geolokatsiya", callback_data="vedit_geo")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="vedit_cancel")],
    ])
    await query.edit_message_text("Qaysi qismini o'zgartirmoqchisiz?", reply_markup=keyboard)
    return VAC_EDIT_CHOOSE


async def vac_edit_choose_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field = query.data.removeprefix("vedit_")

    if field == "cancel":
        context.user_data.pop("vac", None)
        await query.edit_message_text("❌ Vakansiya yaratish bekor qilindi.")
        await query.message.reply_text("Menyuga qaytish:", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    if field == "geo":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ O'tkazib yuborish", callback_data="geo_skip")],
        ])
        await query.edit_message_text("📍 Yangi geolokatsiya yuboring yoki o'tkazib yuboring:", reply_markup=keyboard)
        return VAC_GEO

    labels = {
        "title": "Vakansiya nomi",
        "headcount": "Necha kishi kerak (raqam)",
        "date_text": "Sana",
        "location_text": "Lokatsiya (yozma)",
        "work_time": "Ish vaqti",
        "salary": "Xizmati haqi",
        "deposit": "Bron qilish narxi",
    }
    context.user_data["vac_editing_field"] = field
    await query.edit_message_text(f"Yangi qiymatni kiriting — {labels.get(field, field)}:")
    return VAC_EDIT_VALUE


async def vac_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field = context.user_data.pop("vac_editing_field", None)
    if not field:
        return VAC_PREVIEW

    value = update.message.text.strip()
    if field == "headcount":
        try:
            value = int(value)
            if value < 1:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Iltimos, to'g'ri raqam kiriting:")
            context.user_data["vac_editing_field"] = field
            return VAC_EDIT_VALUE

    context.user_data["vac"][field] = value
    return await _show_preview(update, context)


async def vac_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("vac", None)
    await update.message.reply_text("❌ Vakansiya yaratish bekor qilindi.", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END


# ══════════════════════════════════════════════
#  STATISTICS
# ══════════════════════════════════════════════

async def stats_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    vacancies = db_get_all_vacancies()
    if not vacancies:
        await update.message.reply_text("📊 Hozircha vakansiyalar yo'q.")
        return

    buttons = []
    for v in vacancies:
        label = f"#{v['id']} {v['title']} ({v['remaining']}/{v['headcount']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"stat_{v['id']}")])

    await update.message.reply_text(
        "📊 Vakansiyani tanlang:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def stats_vacancy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    vid = int(query.data.removeprefix("stat_"))
    v = db_get_vacancy(vid)
    if not v:
        await query.edit_message_text("⚠️ Vakansiya topilmadi.")
        return

    confirmed_bookings = db_get_bookings_for_vacancy(vid, confirmed=1)
    pending_bookings = db_get_bookings_for_vacancy(vid, confirmed=0)
    # Filter pending that have receipt
    pending_with_receipt = [b for b in pending_bookings if b.get("receipt_file_id")]
    pending_no_receipt = [b for b in pending_bookings if not b.get("receipt_file_id")]

    text = f"📊 #{v['id']} — {v['title']}\n"
    text += f"Sana: {v['date_text']}\n"
    text += f"Bo'sh joylar: {v['remaining']}/{v['headcount']}\n\n"

    # Confirmed
    text += f"✅ Bron qilganlar: {len(confirmed_bookings)}\n"
    buttons = []
    for i, b in enumerate(confirmed_bookings, 1):
        u = db_get_user(b["user_id"])
        if u:
            text += f"  {i}. {user_mention(u)}\n"
            if b.get("receipt_file_id"):
                buttons.append([InlineKeyboardButton(
                    f"📄 Chek {i} — {u.get('name', '?')}",
                    callback_data=f"showcheck_{b['id']}",
                )])

    # Pending with receipt
    if pending_with_receipt:
        text += f"\n⏳ Cheki tekshirilmoqda: {len(pending_with_receipt)}\n"
        for i, b in enumerate(pending_with_receipt, 1):
            u = db_get_user(b["user_id"])
            if u:
                text += f"  {i}. {user_mention(u)}\n"
                buttons.append([InlineKeyboardButton(
                    f"📄 Pending chek {i} — {u.get('name', '?')}",
                    callback_data=f"showcheck_{b['id']}",
                )])

    # Pending without receipt
    if pending_no_receipt:
        text += f"\n🕐 Chek kutilmoqda: {len(pending_no_receipt)}\n"
        for i, b in enumerate(pending_no_receipt, 1):
            u = db_get_user(b["user_id"])
            if u:
                text += f"  {i}. {user_mention(u)}\n"

    if v["remaining"] <= 0:
        text += "\n🔴 Bu vakansiya bo'yicha ishchilar ro'yxati to'lgan."

    kb = InlineKeyboardMarkup(buttons) if buttons else None
    await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def show_check_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    bid = int(query.data.removeprefix("showcheck_"))
    booking = db_get_booking(bid)
    if not booking or not booking.get("receipt_file_id"):
        await query.answer("Chek topilmadi.", show_alert=True)
        return

    u = db_get_user(booking["user_id"])
    caption = f"Booking #{bid}\n{u.get('name', '?')} — {u.get('phone', '?')}"
    await context.bot.send_photo(
        chat_id=query.from_user.id,
        photo=booking["receipt_file_id"],
        caption=caption,
    )


# ══════════════════════════════════════════════
#  ADMIN MESSAGING
# ══════════════════════════════════════════════

async def msg_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Bitta foydalanuvchiga", callback_data="msgt_one")],
        [InlineKeyboardButton("✅ Vakansiya bron qilganlarga", callback_data="msgt_confirmed")],
        [InlineKeyboardButton("⏳ Vakansiya pendingdagilarga", callback_data="msgt_pending")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="msgt_cancel")],
    ])
    await update.message.reply_text(
        "✉️ Kimga xabar yubormoqchisiz?",
        reply_markup=keyboard,
    )
    return MSG_CHOOSE_TARGET


async def msg_target_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target = query.data.removeprefix("msgt_")

    if target == "cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        await query.message.reply_text("Menyu:", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    context.user_data["msg_target"] = target

    if target == "one":
        # Show user list with pagination
        return await _show_user_list(query, context, page=0)
    else:
        # Show vacancy list
        vacancies = db_get_all_vacancies()
        if not vacancies:
            await query.edit_message_text("Vakansiyalar yo'q.")
            return ConversationHandler.END
        buttons = []
        for v in vacancies:
            buttons.append([InlineKeyboardButton(
                f"#{v['id']} {v['title']}", callback_data=f"msgvac_{v['id']}",
            )])
        buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="msgvac_cancel")])
        await query.edit_message_text("Vakansiyani tanlang:", reply_markup=InlineKeyboardMarkup(buttons))
        return MSG_CHOOSE_VACANCY


USERS_PER_PAGE = 10


async def _show_user_list(query, context: ContextTypes.DEFAULT_TYPE, page: int) -> int:
    total = db_count_users()
    offset = page * USERS_PER_PAGE
    users = db_get_all_users(limit=USERS_PER_PAGE, offset=offset)

    if not users:
        await query.edit_message_text("Foydalanuvchilar yo'q.")
        return ConversationHandler.END

    buttons = []
    for u in users:
        label = f"{u.get('name', 'Nomsiz')} — {u.get('phone', '?')}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"msgusr_{u['user_id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"msgpage_{page - 1}"))
    if offset + USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"msgpage_{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="msgusr_cancel")])

    context.user_data["msg_page"] = page
    await query.edit_message_text(
        f"Foydalanuvchini tanlang ({offset + 1}-{min(offset + USERS_PER_PAGE, total)}/{total}):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return MSG_CHOOSE_USER


async def msg_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.removeprefix("msgpage_"))
    return await _show_user_list(query, context, page)


async def msg_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data.removeprefix("msgusr_")

    if data == "cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        await query.message.reply_text("Menyu:", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    uid = int(data)
    context.user_data["msg_recipients"] = [uid]
    u = db_get_user(uid)
    name = u.get("name", "?") if u else "?"
    await query.edit_message_text(f"Tanlandi: {name}\n\nYuboriladigan xabarni yozing:")
    return MSG_TEXT


async def msg_vacancy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data.removeprefix("msgvac_")

    if data == "cancel":
        await query.edit_message_text("❌ Bekor qilindi.")
        await query.message.reply_text("Menyu:", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    vid = int(data)
    target = context.user_data.get("msg_target")

    if target == "confirmed":
        bookings = db_get_bookings_for_vacancy(vid, confirmed=1)
    else:  # pending
        bookings = db_get_bookings_for_vacancy(vid, confirmed=0)

    recipients = [b["user_id"] for b in bookings]
    if not recipients:
        await query.edit_message_text("Bu vakansiya bo'yicha foydalanuvchilar topilmadi.")
        await query.message.reply_text("Menyu:", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    context.user_data["msg_recipients"] = recipients
    await query.edit_message_text(
        f"{len(recipients)} ta foydalanuvchiga xabar yuboriladi.\n\nXabar matnini yozing:"
    )
    return MSG_TEXT


async def msg_send_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    recipients = context.user_data.get("msg_recipients", [])

    sent = 0
    failed = 0
    for uid in recipients:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Yuborildi: {sent}\n❌ Yuborilmadi: {failed}",
        reply_markup=admin_reply_keyboard(),
    )
    context.user_data.pop("msg_recipients", None)
    context.user_data.pop("msg_target", None)
    return ConversationHandler.END


async def msg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_reply_keyboard())
    return ConversationHandler.END


# ══════════════════════════════════════════════
#  GENERAL TEXT HANDLER
# ══════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    # Admin rejection reason
    if is_admin(user.id) and context.user_data.get("awaiting_reject_reason"):
        await _handle_reject_reason(update, context)
        return

    # Non-admin users
    if not is_admin(user.id):
        db_user = db_get_user(user.id)
        if not db_user:
            await update.message.reply_text("Iltimos, /start buyrug'ini yuboring.")
        elif db_user.get("approved"):
            await update.message.reply_text(
                "Kanaldagi vakansiyalarni ko'ring va \"Ishni bron qilish\" tugmasini bosing."
            )
        else:
            await update.message.reply_text("⏳ Sizning arizangiz ko'rib chiqilmoqda.")


# ══════════════════════════════════════════════
#  /myid  &  /help
# ══════════════════════════════════════════════

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"🆔 Sizning Telegram ID: {update.effective_user.id}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = "📋 Buyruqlar:\n\n/start — Botni ishga tushirish\n/myid — Telegram ID\n/help — Yordam\n"
    if is_admin(user.id):
        text += (
            "\n👑 Admin:\n"
            "\"📝 Yangi vakansiya qo'shish\" — Vakansiya yaratish\n"
            "\"📊 Statistika\" — Vakansiya statistikasi\n"
            "\"✉️ Foydalanuvchiga yozish\" — Xabar yuborish\n"
            "/cancel — Bekor qilish\n"
        )
    await update.message.reply_text(text)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    kb = admin_reply_keyboard() if is_admin(update.effective_user.id) else ReplyKeyboardRemove()
    await update.message.reply_text("❌ Bekor qilindi.", reply_markup=kb)
    return ConversationHandler.END


# ══════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)


# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════

def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set!")
        return

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Registration conversation ──
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_PHONE: [
                MessageHandler(filters.CONTACT, reg_phone),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone),
            ],
            REG_OFFERTA: [CallbackQueryHandler(reg_offerta_cb, pattern=r"^offerta_")],
            REG_PASSPORT: [MessageHandler(filters.PHOTO, reg_passport)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    # ── Vacancy creation conversation ──
    vac_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"(?i)📝\s*yangi vakansiya") & filters.ChatType.PRIVATE,
                vac_start,
            ),
            CommandHandler("new_vacancy", vac_start),
        ],
        states={
            VAC_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_title)],
            VAC_HEADCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_headcount)],
            VAC_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_date)],
            VAC_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_location)],
            VAC_WORKTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_worktime)],
            VAC_SALARY: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_salary)],
            VAC_DEPOSIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, vac_deposit)],
            VAC_GEO: [
                MessageHandler(filters.LOCATION, vac_geo_received),
                CallbackQueryHandler(vac_geo_skip_cb, pattern=r"^geo_skip$"),
            ],
            VAC_PREVIEW: [
                CallbackQueryHandler(vac_confirm_cb, pattern=r"^vac_confirm$"),
                CallbackQueryHandler(vac_edit_cb, pattern=r"^vac_edit$"),
            ],
            VAC_EDIT_CHOOSE: [
                CallbackQueryHandler(vac_edit_choose_cb, pattern=r"^vedit_"),
            ],
            VAC_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vac_edit_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", vac_cancel)],
    )

    # ── Admin messaging conversation ──
    msg_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"(?i)✉️\s*foydalanuvchiga yozish") & filters.ChatType.PRIVATE,
                msg_start,
            ),
        ],
        states={
            MSG_CHOOSE_TARGET: [
                CallbackQueryHandler(msg_target_cb, pattern=r"^msgt_"),
            ],
            MSG_CHOOSE_VACANCY: [
                CallbackQueryHandler(msg_vacancy_cb, pattern=r"^msgvac_"),
            ],
            MSG_CHOOSE_USER: [
                CallbackQueryHandler(msg_user_cb, pattern=r"^msgusr_"),
                CallbackQueryHandler(msg_page_cb, pattern=r"^msgpage_"),
            ],
            MSG_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_send_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", msg_cancel)],
    )

    # ── Register handlers (ORDER MATTERS) ──
    app.add_handler(vac_conv)
    app.add_handler(msg_conv)
    app.add_handler(reg_conv)

    # Callback queries (outside conversations)
    app.add_handler(CallbackQueryHandler(app_approve_cb, pattern=r"^app_approve_"))
    app.add_handler(CallbackQueryHandler(app_reject_cb, pattern=r"^app_reject_"))
    app.add_handler(CallbackQueryHandler(pay_approve_cb, pattern=r"^pay_approve_"))
    app.add_handler(CallbackQueryHandler(pay_reject_cb, pattern=r"^pay_reject_"))
    app.add_handler(CallbackQueryHandler(stats_vacancy_cb, pattern=r"^stat_"))
    app.add_handler(CallbackQueryHandler(show_check_cb, pattern=r"^showcheck_"))

    # Commands
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Stats button
    app.add_handler(MessageHandler(
        filters.Regex(r"(?i)📊\s*statistika") & filters.ChatType.PRIVATE,
        stats_start,
    ))

    # Photo handler (receipts from approved users)
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))

    # General text handler (catch-all)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_text))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("Bot starting... (polling)")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
