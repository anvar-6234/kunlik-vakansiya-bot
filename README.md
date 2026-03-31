# Kunlik Vakansiya Telegram Bot

Bu repozitoriy Telegram botni yaratish uchun barcha kod va fayllarni o‘z ichiga oladi. Bot kunlik vakansiyalarni kanalga joylaydi, foydalanuvchilardan bron qilish uchun ro‘yxatdan o‘tishni talab qiladi, adminlarga ro‘yxatni tasdiqlash yoki rad etish imkoniyatini beradi va bron to‘lovlarini boshqaradi. 

## Funktsiyalar

- **Vakansiya joylash** – Administratorlar `new_vacancy` buyruqini berish orqali yangi vakansiya qo‘sha oladi. Bot ulardan vakansiya nomi, kerakli xodimlar soni, manzil, ish vaqti, to‘lov miqdori, bron narxi va (ixtiyoriy) geolokatsiyani so‘raydi, so‘ngra kanalga formatlangan e’lon bilan birga `Ishni bron qilish` tugmasini yuboradi.

- **Foydalanuvchini ro‘yxatdan o‘tkazish** – Foydalanuvchi e’londagi tugmani bosganda, agar ro‘yxatdan o‘tmagan bo‘lsa, ism familiya, telefon raqami, ommaviy oferta matnini qabul qilish va pasport suratini yuborish jarayoni orqali ro‘yxatdan o‘tadi. Ro‘yxat ma’lumotlari adminlarga yuboriladi.

- **Admin tasdig‘i** – Adminlar yangi foydalanuvchi ma’lumotlarini ko‘rishadi va `Tasdiqlayman` yoki `Tasdiqlamayman` tugmalaridan birini tanlash orqali qaror qabul qilishadi. Tasdiqlangan foydalanuvchi uchun xush kelibsiz xabar yuboriladi. Rad etilgan foydalanuvchi holatida admin sababini yozishi kerak; bu sabab foydalanuvchiga yuboriladi.

- **Bron qilish va to‘lov** – Tasdiqlangan foydalanuvchi uchun bot bron to‘lovini o‘tkazish uchun kartaning raqami va ism familiyasini ko‘rsatadi. Foydalanuvchi to‘lov chekin yuboradi; bu cheklar adminlarga yuboriladi. Admin tasdiqlasa, vakansiyaning qolgan joylari kamayadi va joylar tugagach postning oxiriga `Ish joylari qolmadi! Rahmat )` degan matn qo‘shiladi.

- **Ma’lumotlarni saqlash** – Bot ma’lumotlar (foydalanuvchilar, vakansiyalar va bronlar) ni `data/vacancy_bot.sqlite3` faylida SQLite orqali saqlaydi. Agar kerak bo‘lsa, ma’lumotlarni boshqa tizimga eksport qilish oson.

## O‘rnatish

1. **Botni yaratish**. Telegram’da [@BotFather](https://t.me/BotFather) orqali yangi bot yarating va API token oling.
2. **Kodni yuklab olish**. Ushbu repozitoriydan fayllarni klonlab oling yoki arxiv sifatida yuklab oling.
3. **Muhitni sozlash**:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Konfiguratsiya**. Quyidagi muhit o‘zgaruvchilarini `.env` fayliga yozing yoki xosting platformasiga kiritib qo‘ying:

   - `BOT_TOKEN` – BotFather’dan olgan token
   - `ADMIN_IDS` – Admin Telegram ID’lari, vergul bilan ajratilgan (masalan, `12345,67890`)
   - `CHANNEL_ID` – Vakansiyalar e’lon qilinadigan kanalning ID’si (manfiy butun son). Kanalga botni admin qilib qo‘shishni unutmang.
   - `CARD_NUMBER` – Bron to‘lovini qabul qiluvchi bank kartasi raqami
   - `CARD_HOLDER` – Kartaning egasi
   - `DATA_DIR` (ixtiyoriy) – SQLite fayli saqlanadigan papka (standart: `data`)

5. **Botni ishga tushirish**:

   ```bash
   python vacancy_bot.py
   ```

Bot `polling` rejimida ishlaydi. Uzoq muddatli xosting uchun [Railway](https://railway.app), [Render](https://render.com), yoki [Fly.io](https://fly.io) kabi bepul serverlarni sinab ko‘rishingiz mumkin. Xostingda botni avtomatik ishga tushirish uchun Dockerfile yaratish yoki PM2/forever vositalaridan foydalanish mumkin.

## Foydalanish

- **/start** – Oddiy foydalanuvchilarga bot haqida qisqacha ma’lumot beradi.
- **/new_vacancy** – Admin tomonidan yuboriladi; yangi vakansiya e’lon qilish jarayonini boshlaydi.
- **/cancel** – Har qanday jarayonni bekor qilish uchun. 

**Eslatma**: Foydalanuvchi ma’lumotlari maxfiydir. Bot faqat adminlar bilan almashadi va tashqi tizimlarga uzatilmaydi. Bu bot namunaviy bo‘lib, ehtiyojingizga qarab kengaytirishingiz mumkin.