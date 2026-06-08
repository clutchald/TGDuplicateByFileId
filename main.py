from alphagram import Client, filters, idle
from alphagram.errors import FloodWait
from pymongo import MongoClient, errors
from flask import Flask, jsonify
import threading
import time
import asyncio
import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", '')
MONGO_DB_URI = os.environ.get("MONGO_DB_URI")
mongo = MongoClient(MONGO_DB_URI)
db = mongo["tg_dedup"]
col = db["files"]

progress = {}

app = Client("DEX-DUP-BYFID", bot_token=BOT_TOKEN, use_default_api=True)
web = Flask(__name__)

def get_media(msg):
    media_types = ["photo", "video", "document", "audio", "voice", "animation", "sticker"]
    for m_type in media_types:
        media = getattr(msg, m_type, None)
        if media:
            return getattr(media, "file_unique_id", None)
    return None

def get_prog_bar(percent):
    done = int(percent / 10)
    return "▬" * done + "▭" * (10 - done)

@web.route("/progress/<int:chat_id>")
def get_progress(chat_id):
    return jsonify(progress.get(chat_id, {"status": "idle"}))

@web.route('/')
def index_handler():
    return "HELLO UNHEX!"

@app.on_message(filters.command("clear"))
async def clear_handler(_, m):
    try:
        args = m.text.split()
        if len(args) != 4:
            raise ValueError
        chat_id = int(args[1])
        st = int(args[2])
        en = int(args[3])
    except:
        await m.reply("❌ **Usage:**\n`/clear -100123456 1 5000`\n\n(Chat ID, Start ID, End ID)")
        return

    status_msg = await m.reply("🔍 **Initializing Scan...**")

    try:
        col.delete_many({"chat": chat_id})
    except:
        pass

    total = en - st + 1
    done = 0
    deleted_count = 0
    to_delete = []
    last_update = 0

    progress[chat_id] = {"total": total, "done": 0, "deleted": 0, "percent": 0, "status": "running"}

    for i in range(st, en + 1, 200):
        ids = list(range(i, min(i + 200, en + 1)))

        msgs = None
        while not msgs:
            try:
                msgs = await app.get_messages(chat_id, ids)
            except FloodWait as e:
                await asyncio.sleep(e.value if isinstance(e.value, int) else 35)
            except Exception:
                break

        if not isinstance(msgs, list): msgs = [msgs]

        for msg in msgs:
            done += 1
            if not msg or msg.empty:
                continue

            fid = get_media(msg)
            if not fid:
                continue

            exists = col.find_one({"fid": fid})

            if exists:
                to_delete.append(msg.id)
            else:
                col.insert_one({"chat": chat_id, "chat_name": getattr(msg.chat, "title", str(chat_id)), "fid": fid})

            if len(to_delete) >= 100:
                try:
                    await app.delete_messages(chat_id, to_delete)
                    deleted_count += len(to_delete)
                    to_delete = []
                except:
                    pass

            percent = round(done * 100 / total, 2)
            progress[chat_id].update({"done": done, "deleted": deleted_count, "percent": percent})

            if (time.time() - last_update) > 5:
                bar = get_prog_bar(percent)
                text = (
                    f"⚡ **Cleaning Duplicates...**\n"
                    f"📦 `[{bar}]` **{percent}%**\n\n"
                    f"🔹 **Processed:** `{done}/{total}`\n"
                    f"🗑 **Deleted:** `{deleted_count}`\n"
                    f"🎯 **Target ID:** `{chat_id}`"
                )
                try:
                    await status_msg.edit(text)
                    last_update = time.time()
                except: pass

    if to_delete:
        try:
            await app.delete_messages(chat_id, to_delete)
            deleted_count += len(to_delete)
        except:
            pass

    progress[chat_id]["status"] = "done"
    await status_msg.edit(
        f"✅ **Cleanup Complete!**\n\n"
        f"📊 **Final Stats:**\n"
        f"• Total Scanned: `{done}`\n"
        f"• Duplicates Removed: `{deleted_count}`"
    )

@app.on_message(filters.media & ~filters.private)
async def watcher(_, m):
    fid = get_media(m)
    if not fid: return

    exists = col.find_one({"fid": fid})
    if exists:
        try:
            await m.delete()
        except: pass
    else:
        col.insert_one({"chat": m.chat.id, "chat_name": getattr(m.chat, "title", str(m.chat.id)), "fid": fid})

@app.on_message(filters.command("uid"))
async def get_uid_handler(_, m):
    if not m.reply_to_message:
        await m.reply("❌ **Please reply to a media message to get its Unique ID.**")
        return

    fid = get_media(m.reply_to_message)
    if fid:
        await m.reply(f"🆔 **File Unique ID:**\n\n`{fid}`")
    else:
        await m.reply("❌ **Could not find a valid unique ID.**")



@app.on_message(filters.command("channels"))
async def channels_handler(_, m):
    channels = col.distinct("chat")
    if not channels:
        return await m.reply("❌ No scanned channels found.")
    text = "📂 Scanned Channels\n\n"
    for cid in channels:
        data = col.find_one({"chat": cid}) or {}
        text += f'• {data.get("chat_name","Unknown")}\n`{cid}`\n\n'
    await m.reply(text)

@app.on_message(filters.command("removechannel"))
async def remove_channel_handler(_, m):
    try:
        cid = int(m.command[1])
    except:
        return await m.reply("Usage:\n/removechannel -100xxxxxxxxxx")

    result = col.delete_many({"chat": cid})
    await m.reply(f"✅ Channel removed.\nDeleted {result.deleted_count} records.")

def run_flask():
    web.run("0.0.0.0", 8000)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    app.run()
