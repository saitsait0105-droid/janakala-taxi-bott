import logging
import os
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
# Telegram numeric chat id of the admin — the bot sends intercity-access requests here.
# Get yours by messaging @userinfobot on Telegram.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

# Conversation states
(CHOOSE_ROLE, SHARE_PHONE, ASK_ROUTE, ASK_ITEM,
 ASK_FROM, ASK_TO, ASK_TIME, ASK_PRICE) = range(8)

ROLE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🧍 Жолаушымын", callback_data="role_client")],
    [InlineKeyboardButton("🚕 Жүргізушімін", callback_data="role_driver")],
])

CLIENT_MENU = ReplyKeyboardMarkup(
    [["🚕 Такси шақыру"], ["📦 Жеткізу шақыру"], ["📋 Менің сұраныстарым"]], resize_keyboard=True
)

ROUTE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🏙 Жанақала ішінде", callback_data="route_local")],
    [InlineKeyboardButton("🛣 Жанақала — Орал", callback_data="route_intercity")],
])

SERVICE_LABEL = {"taxi": "🚕 Такси", "delivery": "📦 Жеткізу"}
ROUTE_LABEL = {"local": "🏙 Жанақала ішінде", "intercity": "🛣 Жанақала — Орал"}


def driver_menu(is_online: bool) -> ReplyKeyboardMarkup:
    toggle = "🔴 Offline болу" if is_online else "🟢 Online болу"
    return ReplyKeyboardMarkup(
        [[toggle], ["📋 Ашық тапсырыстар"], ["💰 Табысым", "📜 Тарихым"]], resize_keyboard=True
    )


def contact_name(user: dict) -> str:
    return user.get("phone") or (f"@{user['username']}" if user.get("username") else user.get("full_name") or "белгісіз")


def maps_link(lat, lon):
    if lat is None or lon is None:
        return None
    return f"https://maps.google.com/?q={lat},{lon}"


def rating_str(driver_id) -> str:
    r = db.get_driver_rating(driver_id)
    if r["count"] == 0:
        return "(әлі бағаланбаған)"
    return f"⭐{r['avg']} ({r['count']} баға)"


async def reject_if_blocked(update: Update, tg_user) -> bool:
    """Returns True (and replies) if the user is blocked, so the caller should stop."""
    if db.is_blocked(tg_user.id):
        await update.effective_message.reply_text("🚫 Сіз әкімші тарапынан бұғатталғансыз. Сұрақ болса, әкімшіге хабарласыңыз.")
        return True
    return False


# ---------- ONBOARDING ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if await reject_if_blocked(update, tg_user):
        return ConversationHandler.END
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    if user["role"] and user["phone"]:
        menu = CLIENT_MENU if user["role"] == "client" else driver_menu(bool(user["is_online"]))
        await update.message.reply_text(
            f"Қайта қош келдің, {tg_user.first_name}! Жанақала такси боты жұмыс істеп тұр.",
            reply_markup=menu,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "Сәлем! Бұл — Жанақала ауданының такси боты.\nСен кімсің?",
        reply_markup=ROLE_KEYBOARD,
    )
    return CHOOSE_ROLE


async def choose_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = "client" if query.data == "role_client" else "driver"
    db.set_role(update.effective_user.id, role)
    context.user_data["role"] = role

    phone_button = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Нөмірімді жіберу", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await query.message.reply_text(
        "Байланысу үшін телефон нөміріңді жібер:",
        reply_markup=phone_button,
    )
    return SHARE_PHONE


async def share_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact:
        db.set_phone(update.effective_user.id, contact.phone_number)
    role = context.user_data.get("role")
    menu = CLIENT_MENU if role == "client" else driver_menu(False)
    await update.message.reply_text("Дайын! Енді мәзірді қолдана аласың.", reply_markup=menu)
    return ConversationHandler.END


# ---------- DRIVER: online/offline ----------

async def toggle_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if await reject_if_blocked(update, tg_user):
        return
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    new_status = not bool(user["is_online"])
    db.set_online(tg_user.id, new_status)
    if new_status:
        await update.message.reply_text(
            "🟢 Сен онлайнсың! Жаңа тапсырыстар келгенде хабарлаймын.",
            reply_markup=driver_menu(True),
        )
    else:
        await update.message.reply_text(
            "🔴 Сен офлайнсың. Жаңа тапсырыстар келмейді.",
            reply_markup=driver_menu(False),
        )


# ---------- CLIENT: create a ride request ----------

LOCATION_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Локация жіберу", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True,
)


async def new_ride_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await reject_if_blocked(update, update.effective_user):
        return ConversationHandler.END
    service_type = "delivery" if "Жеткізу" in update.message.text else "taxi"
    context.user_data["service_type"] = service_type
    await update.message.reply_text(
        "Бағытты таңда:",
        reply_markup=ROUTE_KEYBOARD,
    )
    return ASK_ROUTE


async def choose_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user

    if query.data == "route_intercity" and not db.has_intercity_access(tg_user.id):
        user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
        await query.edit_message_text(
            "🛣 'Жанақала — Орал' бағыты бойынша тапсырыс беру үшін әкімшінің рұқсаты керек.\n"
            "Сұранысыңызды әкімшіге жібердім, растаған соң хабарласамын."
        )
        if ADMIN_CHAT_ID:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Рұқсат беру", callback_data=f"icgrant_{tg_user.id}"),
                InlineKeyboardButton("❌ Бас тарту", callback_data=f"icdeny_{tg_user.id}"),
            ]])
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"🔔 {contact_name(user)} 'Жанақала — Орал' бағытына рұқсат сұрап тұр.\n"
                        f"Қызмет түрі: {SERVICE_LABEL.get(context.user_data.get('service_type'), '-')}"
                    ),
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning(f"Could not notify admin: {e}")
        else:
            logger.warning("ADMIN_CHAT_ID орнатылмаған — intercity сұранысы ешкімге жіберілмеді.")
        return ConversationHandler.END

    context.user_data["route"] = "intercity" if query.data == "route_intercity" else "local"

    if context.user_data.get("service_type") == "delivery":
        await query.edit_message_text("Не жеткізу керек? (қысқаша сипаттама жаз)")
        return ASK_ITEM

    await query.edit_message_text("Бағыт таңдалды. Енді толтырайық.")
    await query.message.reply_text(
        "Қайдан шығасың? 📍 Локацияңды жібер немесе мекенжайды өзің жаз (мыс. Жанақала, орталық):",
        reply_markup=LOCATION_KEYBOARD,
    )
    return ASK_FROM


async def ask_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["item_note"] = update.message.text
    await update.message.reply_text(
        "Затты қайдан алу керек? 📍 Локацияңды жібер немесе мекенжайды өзің жаз:",
        reply_markup=LOCATION_KEYBOARD,
    )
    return ASK_FROM


async def ask_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        loc = update.message.location
        context.user_data["from"] = "📍 Локация (карта арқылы)"
        context.user_data["from_lat"] = loc.latitude
        context.user_data["from_lon"] = loc.longitude
    else:
        context.user_data["from"] = update.message.text
        context.user_data["from_lat"] = None
        context.user_data["from_lon"] = None
    await update.message.reply_text(
        "Қайда барасың? 📍 Локация жіберуге болады немесе мекенжайды жаз:",
        reply_markup=LOCATION_KEYBOARD,
    )
    return ASK_TO


async def ask_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location:
        loc = update.message.location
        context.user_data["to"] = "📍 Локация (карта арқылы)"
        context.user_data["to_lat"] = loc.latitude
        context.user_data["to_lon"] = loc.longitude
    else:
        context.user_data["to"] = update.message.text
        context.user_data["to_lat"] = None
        context.user_data["to_lon"] = None
    await update.message.reply_text("Қай уақытта? (мыс. бүгін 14:00)", reply_markup=ReplyKeyboardRemove())
    return ASK_TIME


async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["time"] = update.message.text
    await update.message.reply_text("Жол ақысын өзің жаз (теңге):")
    return ASK_PRICE


async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["price"] = update.message.text
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    service_type = context.user_data.get("service_type", "taxi")
    route = context.user_data.get("route", "local")
    item_note = context.user_data.get("item_note")

    ride_id = db.create_ride(
        client_id=user["id"],
        from_location=context.user_data["from"],
        to_location=context.user_data["to"],
        ride_time=context.user_data["time"],
        price=context.user_data["price"],
        from_lat=context.user_data.get("from_lat"),
        from_lon=context.user_data.get("from_lon"),
        to_lat=context.user_data.get("to_lat"),
        to_lon=context.user_data.get("to_lon"),
        service_type=service_type,
        route=route,
        item_note=item_note,
    )
    from_link = maps_link(context.user_data.get("from_lat"), context.user_data.get("from_lon"))
    to_link = maps_link(context.user_data.get("to_lat"), context.user_data.get("to_lon"))

    header = f"{SERVICE_LABEL.get(service_type)} | {ROUTE_LABEL.get(route)}"
    item_line = f"\n📦 Не: {item_note}" if item_note else ""

    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Болдырмау", callback_data=f"clientcancel_{ride_id}")]])
    await update.message.reply_text(
        f"✅ Тапсырыс жасалды (№{ride_id})! {header}{item_line}\n"
        f"{context.user_data['from']} → {context.user_data['to']}\n"
        f"Уақыты: {context.user_data['time']}\n"
        f"Бағасы: {context.user_data['price']} тг\n\n"
        f"Онлайн жүргізушілерге жіберілді, жауап күтеміз.",
        reply_markup=CLIENT_MENU,
    )
    await update.message.reply_text("Тапсырысты болдырмау керек болса:", reply_markup=cancel_kb)

    # broadcast to all online drivers
    driver_ids = db.get_online_driver_telegram_ids()
    text = (
        f"🆕 Жаңа тапсырыс №{ride_id} — {header}{item_line}\n"
        f"{context.user_data['from']} → {context.user_data['to']}\n"
        f"Уақыты: {context.user_data['time']}\n"
        f"Бағасы: {context.user_data['price']} тг"
    )
    if from_link:
        text += f"\n📍 Қайдан алу керек: {from_link}"
    if to_link:
        text += f"\n🏁 Бару керек жер: {to_link}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Қабылдау", callback_data=f"accept_{ride_id}")]])
    for driver_tg_id in driver_ids:
        try:
            await context.bot.send_message(chat_id=driver_tg_id, text=text, reply_markup=kb)
        except Exception as e:
            logger.warning(f"Could not notify driver {driver_tg_id}: {e}")

    return ConversationHandler.END


async def my_rides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    rides = [r for r in db.get_all_rides() if r["client_id"] == user["id"]]
    if not rides:
        await update.message.reply_text("Сенде әлі тапсырыс жоқ.")
        return
    status_names = {
        "open": "🕓 күтуде", "accepted": "🚕 жолда", "arrived": "📍 жетті",
        "completed": "🏁 аяқталды", "cancelled": "❌ болдырылмады",
    }
    lines = []
    for r in rides[:10]:
        tag = SERVICE_LABEL.get(r["service_type"], "🚕 Такси")
        if r.get("route") == "intercity":
            tag += " (Орал)"
        line = f"№{r['id']} {tag} {r['from_location']} → {r['to_location']} | {r['price']}тг | {status_names.get(r['status'], r['status'])}"
        if r["status"] == "completed" and r["rating"]:
            line += f" | {'⭐' * r['rating']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines))


async def client_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ride_id = int(query.data.split("_")[1])
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    success = db.cancel_ride_by_client(ride_id, user["id"])
    if not success:
        await query.edit_message_text("Бұл тапсырысты болдырмау мүмкін емес (жол басталып кетуі мүмкін).")
        return
    await query.edit_message_text(f"❌ №{ride_id} тапсырысы болдырылмады.")

    ride = db.get_ride(ride_id)
    if ride.get("driver_id"):
        driver = db.get_user_by_id(ride["driver_id"])
        try:
            await context.bot.send_message(chat_id=driver["telegram_id"], text=f"⚠️ №{ride_id} тапсырысын жолаушы болдырмады.")
        except Exception as e:
            logger.warning(f"Could not notify driver of cancel: {e}")


# ---------- DRIVER: browse, accept, and progress an order ----------

async def open_rides(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if await reject_if_blocked(update, tg_user):
        return
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    if not user["is_online"]:
        await update.message.reply_text("Алдымен 🟢 Online болу керексің.")
        return

    active = db.get_driver_active_ride(user["id"])
    if active:
        await update.message.reply_text(f"Сенде әлі аяқталмаған тапсырыс бар: №{active['id']}. Алдымен соны аяқта.")
        return

    rides = db.get_open_rides()
    if not rides:
        await update.message.reply_text("Қазір ашық тапсырыс жоқ.")
        return
    for r in rides:
        tag = f"{SERVICE_LABEL.get(r['service_type'], '🚕 Такси')} | {ROUTE_LABEL.get(r.get('route'), '🏙 Жанақала ішінде')}"
        item_line = f"\n📦 Не: {r['item_note']}" if r.get("item_note") else ""
        text = (
            f"№{r['id']} — {tag}{item_line}\n"
            f"{r['from_location']} → {r['to_location']}\n"
            f"Уақыты: {r['ride_time']}\n"
            f"Бағасы: {r['price']} тг"
        )
        from_link = maps_link(r.get("from_lat"), r.get("from_lon"))
        to_link = maps_link(r.get("to_lat"), r.get("to_lon"))
        if from_link:
            text += f"\n📍 Қайдан алу керек: {from_link}"
        if to_link:
            text += f"\n🏁 Бару керек жер: {to_link}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Қабылдау", callback_data=f"accept_{r['id']}")]])
        await update.message.reply_text(text, reply_markup=keyboard)


async def accept_ride_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ride_id = int(query.data.split("_")[1])

    tg_user = update.effective_user
    if db.is_blocked(tg_user.id):
        await query.edit_message_text("🚫 Сіз әкімші тарапынан бұғатталғансыз.")
        return
    driver = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    if not driver["is_online"]:
        await query.edit_message_text("Алдымен 🟢 Online болу керексің.")
        return

    active = db.get_driver_active_ride(driver["id"])
    if active:
        await query.edit_message_text(f"Сенде әлі аяқталмаған тапсырыс бар: №{active['id']}.")
        return

    success = db.accept_ride(ride_id, driver["id"])
    if not success:
        await query.edit_message_text("Кешіріңіз, бұл тапсырысты басқа жүргізуші алып қойды.")
        return

    ride = db.get_ride(ride_id)
    client = db.get_user_by_id(ride["client_id"])

    progress_kb = InlineKeyboardMarkup([[InlineKeyboardButton("📍 Жеттім", callback_data=f"arrived_{ride_id}")]])
    from_link = maps_link(ride.get("from_lat"), ride.get("from_lon"))
    accept_text = (
        f"✅ №{ride_id} тапсырысын алдың.\n"
        f"Жолаушы: {contact_name(client)}\n"
        f"{ride['from_location']} → {ride['to_location']}\n\n"
        f"Жолаушыны алуға жеткенде батырманы бас:"
    )
    if from_link:
        accept_text = f"📍 Алу орны: {from_link}\n\n" + accept_text
    await query.edit_message_text(accept_text, reply_markup=progress_kb)

    if ride.get("from_lat") is not None and ride.get("from_lon") is not None:
        try:
            await context.bot.send_location(chat_id=driver["telegram_id"], latitude=ride["from_lat"], longitude=ride["from_lon"])
        except Exception as e:
            logger.warning(f"Could not send location pin: {e}")

    try:
        await context.bot.send_message(
            chat_id=client["telegram_id"],
            text=f"🚕 №{ride_id} тапсырысыңызды жүргізуші қабылдады!\nЖүргізуші: {contact_name(driver)} {rating_str(driver['id'])}\nЖолда келе жатыр.",
        )
    except Exception as e:
        logger.warning(f"Could not notify client: {e}")


async def arrived_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ride_id = int(query.data.split("_")[1])

    tg_user = update.effective_user
    driver = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    success = db.mark_arrived(ride_id, driver["id"])
    if not success:
        await query.edit_message_text("Бұл әрекетті орындау мүмкін емес.")
        return

    ride = db.get_ride(ride_id)
    client = db.get_user_by_id(ride["client_id"])

    finish_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏁 Сапарды аяқтау", callback_data=f"finish_{ride_id}")]])
    await query.edit_message_text(f"📍 №{ride_id}: жолаушыға жеттің. Сапар аяқталғанда батырманы бас:", reply_markup=finish_kb)

    try:
        await context.bot.send_message(chat_id=client["telegram_id"], text=f"📍 Жүргізуші №{ride_id} тапсырысы бойынша жеткен жерге келді!")
    except Exception as e:
        logger.warning(f"Could not notify client: {e}")


async def finish_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ride_id = int(query.data.split("_")[1])

    tg_user = update.effective_user
    driver = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    success = db.complete_ride_by_driver(ride_id, driver["id"])
    if not success:
        await query.edit_message_text("Бұл әрекетті орындау мүмкін емес.")
        return

    ride = db.get_ride(ride_id)
    client = db.get_user_by_id(ride["client_id"])

    await query.edit_message_text(f"🏁 №{ride_id} сапары аяқталды. Рахмет!")

    stars_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐" * n, callback_data=f"rate_{ride_id}_{n}") for n in range(1, 6)
    ]])
    try:
        await context.bot.send_message(
            chat_id=client["telegram_id"],
            text=f"🏁 №{ride_id} сапарыңыз аяқталды. Сау жол!\nЖүргізушіні бағалаңыз:",
            reply_markup=stars_kb,
        )
    except Exception as e:
        logger.warning(f"Could not notify client: {e}")


async def rate_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, ride_id, stars = query.data.split("_")
    ride_id, stars = int(ride_id), int(stars)

    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)

    success = db.rate_ride(ride_id, user["id"], stars)
    if not success:
        await query.edit_message_text("Бұл тапсырысты бағалау мүмкін емес (бұрын бағаланған болуы мүмкін).")
        return
    await query.edit_message_text(f"Рахмет! Сіз №{ride_id} үшін {'⭐' * stars} қойдыңыз.")


async def earnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    stats = db.get_driver_earnings(user["id"])
    rating = db.get_driver_rating(user["id"])
    text = f"💰 Аяқталған сапар саны: {stats['count']}\n💵 Жалпы табыс: {stats['total']} тг"
    if rating["count"]:
        text += f"\n⭐ Рейтинг: {rating['avg']} ({rating['count']} баға)"
    await update.message.reply_text(text)


async def driver_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user = db.get_or_create_user(tg_user.id, tg_user.username, tg_user.full_name)
    rides = db.get_driver_history(user["id"])
    if not rides:
        await update.message.reply_text("Әлі аяқталған сапарың жоқ.")
        return
    lines = []
    for r in rides:
        stars = "⭐" * r["rating"] if r["rating"] else "—"
        lines.append(f"№{r['id']} {r['from_location']} → {r['to_location']} | {r['price']}тг | {stars}")
    await update.message.reply_text("📜 Соңғы сапарларың:\n" + "\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Болдырылмады.")
    return ConversationHandler.END


# ---------- ADMIN: approve/deny intercity access requests (via Telegram) ----------

async def _is_admin(update: Update) -> bool:
    return bool(ADMIN_CHAT_ID) and str(update.effective_user.id) == str(ADMIN_CHAT_ID)


async def intercity_grant_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _is_admin(update):
        await query.answer("Бұл әрекет тек әкімшіге арналған.", show_alert=True)
        return
    target_tg_id = int(query.data.split("_")[1])
    user = db.get_user_by_telegram_id(target_tg_id)
    if not user:
        await query.edit_message_text("Пайдаланушы табылмады.")
        return
    db.set_intercity_access(user["id"], True)
    await query.edit_message_text(f"✅ {contact_name(user)} үшін 'Жанақала — Орал' бағыты рұқсат етілді.")
    try:
        await context.bot.send_message(
            chat_id=target_tg_id,
            text="✅ Сізге 'Жанақала — Орал' бағыты бойынша тапсырыс беруге рұқсат берілді! Тапсырысты қайта бастап көріңіз.",
        )
    except Exception as e:
        logger.warning(f"Could not notify user of intercity grant: {e}")


async def intercity_deny_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _is_admin(update):
        await query.answer("Бұл әрекет тек әкімшіге арналған.", show_alert=True)
        return
    target_tg_id = int(query.data.split("_")[1])
    user = db.get_user_by_telegram_id(target_tg_id)
    if not user:
        await query.edit_message_text("Пайдаланушы табылмады.")
        return
    db.set_intercity_access(user["id"], False)
    await query.edit_message_text(f"❌ {contact_name(user)} үшін сұраныс қабылданбады.")
    try:
        await context.bot.send_message(
            chat_id=target_tg_id,
            text="❌ Өкінішке орай, 'Жанақала — Орал' бағыты бойынша сұранысыңыз қабылданбады.",
        )
    except Exception as e:
        logger.warning(f"Could not notify user of intercity denial: {e}")


def main():
    db.init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    onboarding = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_ROLE: [CallbackQueryHandler(choose_role, pattern="^role_")],
            SHARE_PHONE: [MessageHandler(filters.CONTACT, share_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    new_ride_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🚕 Такси шақыру$"), new_ride_start),
            MessageHandler(filters.Regex("^📦 Жеткізу шақыру$"), new_ride_start),
        ],
        states={
            ASK_ROUTE: [CallbackQueryHandler(choose_route, pattern="^route_")],
            ASK_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_item)],
            ASK_FROM: [MessageHandler(filters.LOCATION | (filters.TEXT & ~filters.COMMAND), ask_from)],
            ASK_TO: [MessageHandler(filters.LOCATION | (filters.TEXT & ~filters.COMMAND), ask_to)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(onboarding)
    app.add_handler(new_ride_conv)
    app.add_handler(MessageHandler(filters.Regex("^📋 Менің сұраныстарым$"), my_rides))
    app.add_handler(MessageHandler(filters.Regex("^📋 Ашық тапсырыстар$"), open_rides))
    app.add_handler(MessageHandler(filters.Regex("^(🟢 Online болу|🔴 Offline болу)$"), toggle_online))
    app.add_handler(MessageHandler(filters.Regex("^💰 Табысым$"), earnings))
    app.add_handler(MessageHandler(filters.Regex("^📜 Тарихым$"), driver_history))
    app.add_handler(CallbackQueryHandler(accept_ride_cb, pattern="^accept_"))
    app.add_handler(CallbackQueryHandler(arrived_cb, pattern="^arrived_"))
    app.add_handler(CallbackQueryHandler(finish_cb, pattern="^finish_"))
    app.add_handler(CallbackQueryHandler(rate_cb, pattern="^rate_"))
    app.add_handler(CallbackQueryHandler(client_cancel_cb, pattern="^clientcancel_"))
    app.add_handler(CallbackQueryHandler(intercity_grant_cb, pattern="^icgrant_"))
    app.add_handler(CallbackQueryHandler(intercity_deny_cb, pattern="^icdeny_"))

    logger.info("Bot started")

    webhook_url = os.environ.get("WEBHOOK_URL")  # e.g. https://your-app.onrender.com — set this on Render
    if webhook_url:
        # Webhook mode: for free hosts like Render, which only offer a free *Web Service*
        # (needs to bind to a port and receive HTTP requests) rather than a free background worker.
        port = int(os.environ.get("PORT", "10000"))
        logger.info(f"Running in WEBHOOK mode on port {port} -> {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{webhook_url.rstrip('/')}/{BOT_TOKEN}",
        )
    else:
        # Polling mode: simplest option, works anywhere with outbound internet (e.g. Railway, your own PC/VPS)
        logger.info("Running in POLLING mode")
        app.run_polling()


if __name__ == "__main__":
    main()
