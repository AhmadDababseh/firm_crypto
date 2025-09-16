import os
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
import database  # <- our db file

# ----------------- LOAD ENV -----------------
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ----------------- STATES -----------------
REPORT_CHOOSING, REPORT_TYPING, CONTACT_TYPING = range(3)

# Load menu JSON
with open("menu.json", "r", encoding="utf-8") as f:
    MENU = json.load(f)

# Store temporary user reports before saving to DB
user_reports = {}
user_services = {}

# ----------------- REPORT MENU -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /report to submit a scam or /contact to reach us.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.message.from_user.id
    user_reports[telegram_id] = {k: None for k in MENU.keys()}  # reset fields

    return await show_report_menu(update, context)

async def show_report_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    report_data = user_reports.get(telegram_id, {})

    keyboard = []
    for key, field in MENU.items():
        icon = "✅" if report_data.get(key) else "❌"
        keyboard.append([InlineKeyboardButton(f"{icon} {field['label']}", callback_data=key)])

    keyboard.append([InlineKeyboardButton("✅ Submit Report", callback_data="submit_report")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text("Fill in the scam report fields:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Fill in the scam report fields:", reply_markup=reply_markup)

    return REPORT_CHOOSING

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    field = query.data

    if field == "submit_report":
        telegram_id = update.effective_user.id
        report_data = user_reports.get(telegram_id)

        # Check required fields
        if not report_data.get("scam_name") or not report_data.get("scam_link"):
            await query.answer("Scam name and scam link are required!")
            return REPORT_CHOOSING

        # Save to DB
        database.add_request(telegram_id, report_data)

        await query.edit_message_text("✅ Scam report submitted successfully!")
        return ConversationHandler.END

    context.user_data["current_field"] = field
    await query.message.reply_text(f"Please enter {MENU[field]['label']}:")
    return REPORT_TYPING

async def received_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.message.from_user.id
    field = context.user_data["current_field"]
    value = update.message.text

    user_reports[telegram_id][field] = value
    return await show_report_menu(update, context)

# ----------------- CONTACT -----------------
async def contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send me your message for Contact Us:")
    return CONTACT_TYPING

async def received_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.message.from_user.id
    message = update.message.text

    # Save contact into services table
    database.add_service(telegram_id, "contact", message)

    await update.message.reply_text("✅ Your message has been sent!")
    return ConversationHandler.END

# ----------------- MAIN -----------------
def main():
    database.init_db()  # ensure tables exist
    app = Application.builder().token(TOKEN).build()

    report_conv = ConversationHandler(
        entry_points=[CommandHandler("report", report)],
        states={
            REPORT_CHOOSING: [CallbackQueryHandler(menu_choice)],
            REPORT_TYPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_field)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    contact_conv = ConversationHandler(
        entry_points=[CommandHandler("contact", contact)],
        states={
            CONTACT_TYPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_contact)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(report_conv)
    app.add_handler(contact_conv)

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
