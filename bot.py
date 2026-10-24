import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "partiler.db")
POST_EVERY_HOURS = 12

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parties (
            chat_id INTEGER NOT NULL,
            party_name TEXT NOT NULL COLLATE NOCASE,
            created_by INTEGER NOT NULL,
            created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, party_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memberships (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            party_name TEXT NOT NULL COLLATE NOCASE,
            user_name TEXT NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_chats (
            chat_id INTEGER PRIMARY KEY,
            next_report_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def display_name(user):
    return user.full_name or user.username or str(user.id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏛️ Parti botuna hoş geldin!\n\n"
        "Komutlar:\n"
        "/parti_kur PartiAdı — parti oluştur\n"
        "/katil PartiAdı — partiye katıl\n"
        "/cik — partinden çık\n"
        "/partiler — partileri ve üyeleri göster\n\n"
        "Not: Türkçe karakterli komutlar yerine /katil ve /cik kullan."
    )

async def create_party(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not context.args:
        await msg.reply_text("Kullanım: /parti_kur PartiAdı\nÖrnek: /parti_kur AKP")
        return
    name = " ".join(context.args).strip()
    if len(name) > 40:
        await msg.reply_text("Parti adı en fazla 40 karakter olabilir.")
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = db()
    try:
        conn.execute(
            "INSERT INTO parties VALUES (?, ?, ?, ?, ?)",
            (chat.id, name, user.id, display_name(user), now)
        )
        conn.execute(
            "INSERT OR REPLACE INTO memberships VALUES (?, ?, ?, ?, ?)",
            (chat.id, user.id, name, display_name(user), now)
        )
        conn.commit()
        await msg.reply_text(f"✅ {name} partisi oluşturuldu. Sen de partiye katıldın!")
    except sqlite3.IntegrityError:
        await msg.reply_text("Bu parti zaten var. Başka bir isim dene.")
    finally:
        conn.close()

async def join_party(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not context.args:
        await msg.reply_text("Kullanım: /katil PartiAdı\nÖrnek: /katil AKP")
        return
    name = " ".join(context.args).strip()
    conn = db()
    found = conn.execute(
        "SELECT party_name FROM parties WHERE chat_id=? AND party_name=?",
        (chat.id, name)
    ).fetchone()
    if not found:
        conn.close()
        await msg.reply_text("Bu parti bulunamadı. /partiler ile partileri gör.")
        return
    conn.execute(
        "INSERT OR REPLACE INTO memberships VALUES (?, ?, ?, ?, ?)",
        (chat.id, user.id, found[0], display_name(user), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()
    await msg.reply_text(f"✅ {user.full_name}, {found[0]} partisine katıldı!")

async def leave_party(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    conn = db()
    row = conn.execute(
        "SELECT party_name FROM memberships WHERE chat_id=? AND user_id=?",
        (chat.id, user.id)
    ).fetchone()
    if not row:
        conn.close()
        await update.effective_message.reply_text("Şu anda herhangi bir partiye üye değilsin.")
        return
    conn.execute("DELETE FROM memberships WHERE chat_id=? AND user_id=?", (chat.id, user.id))
    conn.commit()
    conn.close()
    await update.effective_message.reply_text(f"👋 {row[0]} partisinden çıktın.")

def build_party_list(chat_id):
    conn = db()
    parties = conn.execute(
        "SELECT party_name FROM parties WHERE chat_id=? ORDER BY party_name COLLATE NOCASE",
        (chat_id,)
    ).fetchall()
    lines = ["🏛️ PARTİLER VE ÜYELER", ""]
    if not parties:
        lines.append("Henüz parti oluşturulmamış.")
    for (party_name,) in parties:
        members = conn.execute(
            "SELECT user_name FROM memberships WHERE chat_id=? AND party_name=? ORDER BY user_name COLLATE NOCASE",
            (chat_id, party_name)
        ).fetchall()
        lines.append(f"🔹 {party_name} — {len(members)} üye")
        if members:
            lines.extend(f"  • {member[0]}" for member in members)
        else:
            lines.append("  • Henüz üye yok")
        lines.append("")
    conn.close()
    return "\n".join(lines).strip()

async def list_parties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(build_party_list(update.effective_chat.id))

async def post_report(context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    chats = conn.execute("SELECT chat_id FROM report_chats").fetchall()
    conn.close()
    for (chat_id,) in chats:
        try:
            await context.bot.send_message(chat_id=chat_id, text=build_party_list(chat_id))
        except Exception as e:
            print(f"Could not send report to {chat_id}: {e}")

async def enable_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        await update.effective_message.reply_text("Bu komutu partilerin paylaşılacağı grupta kullan.")
        return
    next_at = (datetime.now(timezone.utc) + timedelta(hours=POST_EVERY_HOURS)).isoformat()
    conn = db()
    conn.execute("INSERT OR REPLACE INTO report_chats VALUES (?, ?)", (chat.id, next_at))
    conn.commit()
    conn.close()
    await update.effective_message.reply_text(
        "⏰ Bu grup için 12 saatlik otomatik parti listesi açıldı. "
        "Botun grupta mesaj gönderme izni olmalı."
    )

async def post_init(application: Application):
    application.job_queue.run_repeating(post_report, interval=POST_EVERY_HOURS * 3600, first=POST_EVERY_HOURS * 3600)

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN ortam değişkeni ayarlanmamış.")
    db().close()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("parti_kur", create_party))
    app.add_handler(CommandHandler("katil", join_party))
    app.add_handler(CommandHandler("cik", leave_party))
    app.add_handler(CommandHandler("partiler", list_parties))
    app.add_handler(CommandHandler("12saat", enable_reports))
    print("Parti botu çalışıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()
