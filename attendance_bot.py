import os
import sqlite3
import math
import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "attendiq.db"

# ----------------------------------------------------
# Database Management
# ----------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_attendance (
            user_id INTEGER PRIMARY KEY,
            attended INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            target INTEGER DEFAULT 75,
            prev_attended INTEGER DEFAULT 0,
            prev_total INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def get_or_create_user(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT attended, total, target FROM user_attendance WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO user_attendance (user_id, attended, total, target, prev_attended, prev_total) VALUES (?, 0, 0, 75, 0, 0)",
            (user_id,)
        )
        conn.commit()
        row = (0, 0, 75)
    conn.close()
    return row

# ----------------------------------------------------
# Calculation Helper (Identical to AttendIQ)
# ----------------------------------------------------
def compute_metrics(attended: int, total: int, target: int = 75):
    if total <= 0:
        return {"percentage": 0.0, "can_bunk": 0, "must_attend": 0, "is_safe": True}

    percentage = (attended / total) * 100.0
    target_ratio = target / 100.0

    if percentage >= target:
        can_bunk = math.floor((attended - (target_ratio * total)) / target_ratio)
        return {
            "percentage": round(percentage, 1),
            "can_bunk": max(0, can_bunk),
            "must_attend": 0,
            "is_safe": True,
        }
    else:
        if target >= 100:
            must_attend = 999
        else:
            must_attend = math.ceil(((target_ratio * total) - attended) / (1.0 - target_ratio))
        return {
            "percentage": round(percentage, 1),
            "can_bunk": 0,
            "must_attend": max(0, must_attend),
            "is_safe": False,
        }

def make_progress_bar(percentage: float, length: int = 10) -> str:
    filled = int(round((min(percentage, 100.0) / 100.0) * length))
    return "█" * filled + "░" * (length - filled)

def build_card_text_and_keyboard(attended: int, total: int, target: int):
    metrics = compute_metrics(attended, total, target)
    bar = make_progress_bar(metrics["percentage"])
    missed = total - attended

    if total == 0:
        advice = "ℹ️ Tap *+1 Present* or *+1 Absent* below to start logging."
    elif metrics["is_safe"]:
        if metrics["can_bunk"] > 0:
            advice = f"🎉 *Safe Zone:* You can safely bunk *{metrics['can_bunk']}* upcoming class(es)!"
        else:
            advice = "⚠️ *On Track:* Do not miss the next class to stay above criteria."
    else:
        advice = f"🚨 *Shortage Alert:* You must attend the next *{metrics['must_attend']}* consecutive class(es) to recover!"

    card = (
        "📊 *Your Attendance Dashboard*\n\n"
        f"• Progress: `{bar}` *{metrics['percentage']}%* (Goal: {target}%)\n"
        f"• Status: *{attended}* attended / *{total}* held (*{missed}* missed)\n\n"
        f"{advice}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Present", callback_data="btn_present"),
            InlineKeyboardButton("➖ Absent", callback_data="btn_absent"),
        ],
        [
            InlineKeyboardButton("↩️ Undo", callback_data="btn_undo"),
            InlineKeyboardButton("🔄 Reset", callback_data="btn_reset"),
        ]
    ])
    return card, keyboard

# ----------------------------------------------------
# Commands
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    attended, total, target = get_or_create_user(user_id)
    card, keyboard = build_card_text_and_keyboard(attended, total, target)

    welcome_msg = (
        "🎓 *Welcome to AttendIQ!*\n"
        "Use the buttons below to log attendance on the fly.\n\n"
        "*Quick Commands:*\n"
        "• `/set <attended> <total>` — Set your numbers manually (e.g., `/set 28 35`)\n"
        "• `/goal <target>` — Change required percentage (e.g., `/goal 80`)\n"
        "• `/status` — View your dashboard anytime\n\n"
    )
    await update.message.reply_text(welcome_msg + card, reply_markup=keyboard, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    attended, total, target = get_or_create_user(user_id)
    card, keyboard = build_card_text_and_keyboard(attended, total, target)
    await update.message.reply_text(card, reply_markup=keyboard, parse_mode="Markdown")

async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("⚠️ *Usage:* `/set <attended> <total>`\n*Example:* `/set 30 38`", parse_mode="Markdown")
        return

    try:
        attended = int(context.args[0])
        total = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Please enter valid integer numbers.")
        return

    if attended > total or attended < 0 or total < 0:
        await update.message.reply_text("❌ Attended classes cannot exceed total classes or be negative.")
        return

    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_attendance 
        SET prev_attended = attended, prev_total = total, attended = ?, total = ? 
        WHERE user_id = ?
    """, (attended, total, user_id))
    conn.commit()
    conn.close()

    _, _, target = get_or_create_user(user_id)
    card, keyboard = build_card_text_and_keyboard(attended, total, target)
    await update.message.reply_text(f"✅ *Updated attendance!*\n\n{card}", reply_markup=keyboard, parse_mode="Markdown")

async def goal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ *Usage:* `/goal <percentage>`\n*Example:* `/goal 80`", parse_mode="Markdown")
        return

    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number (e.g., 75).")
        return

    if target <= 0 or target > 100:
        await update.message.reply_text("❌ Goal must be between 1% and 100%.")
        return

    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE user_attendance SET target = ? WHERE user_id = ?", (target, user_id))
    conn.commit()
    conn.close()

    attended, total, _ = get_or_create_user(user_id)
    card, keyboard = build_card_text_and_keyboard(attended, total, target)
    await update.message.reply_text(f"🎯 *Goal updated to {target}%!*\n\n{card}", reply_markup=keyboard, parse_mode="Markdown")

# ----------------------------------------------------
# Button Clicks (+1, -1, Undo, Reset)
# ----------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    action = query.data

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT attended, total, target, prev_attended, prev_total FROM user_attendance WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    attended, total, target, prev_att, prev_tot = row

    if action == "btn_present":
        cursor.execute("""
            UPDATE user_attendance 
            SET prev_attended = attended, prev_total = total, attended = attended + 1, total = total + 1 
            WHERE user_id = ?
        """, (user_id,))
        attended += 1
        total += 1
    elif action == "btn_absent":
        cursor.execute("""
            UPDATE user_attendance 
            SET prev_attended = attended, prev_total = total, total = total + 1 
            WHERE user_id = ?
        """, (user_id,))
        total += 1
    elif action == "btn_undo":
        cursor.execute("""
            UPDATE user_attendance 
            SET attended = prev_attended, total = prev_total 
            WHERE user_id = ?
        """, (user_id,))
        attended, total = prev_att, prev_tot
    elif action == "btn_reset":
        cursor.execute("""
            UPDATE user_attendance 
            SET prev_attended = attended, prev_total = total, attended = 0, total = 0 
            WHERE user_id = ?
        """, (user_id,))
        attended, total = 0, 0

    conn.commit()
    conn.close()

    card, keyboard = build_card_text_and_keyboard(attended, total, target)
    await query.edit_message_text(card, reply_markup=keyboard, parse_mode="Markdown")

# ----------------------------------------------------
# Main Execution
# ----------------------------------------------------
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("set", set_command))
    app.add_handler(CommandHandler("goal", goal_command))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^btn_"))

    print("Attendance Bot is running... (Press Ctrl+C to stop)")
    app.run_polling()