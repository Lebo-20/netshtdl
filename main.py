import os
import asyncio
import logging
import shutil
import tempfile
import random
from telethon import TelegramClient, events, Button
from dotenv import load_dotenv

load_dotenv()

# Local imports
from api import (
    get_drama_detail, get_all_episodes, get_latest_dramas,
    get_latest_idramas, get_idrama_detail, get_idrama_all_episodes,
    search_dramas, get_subtitle_url
)
from downloader import download_all_episodes
from merge import merge_episodes, split_video
from uploader import upload_drama, sanitize_filename
from database import init_db, is_processed, save_processed_db

# Configuration (Use environment variables or replace these directly)
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS_STR = os.environ.get("ADMIN_ID", "0")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip() and x.strip().lstrip('-').isdigit()]
if not ADMIN_IDS:
    ADMIN_IDS = [0]
ADMIN_ID = ADMIN_IDS[0]
AUTO_CHANNEL = int(os.environ.get("AUTO_CHANNEL", ADMIN_ID)) # Default post to admin
MESSAGE_THREAD_ID = int(os.environ.get("MESSAGE_THREAD_ID", "0")) or None
AUTO_INTERVAL = int(os.environ.get("AUTO_INTERVAL", "900")) # Default 15 mins
PROCESSED_FILE = "processed.json"

print(f"--- BOT CONFIGURATION ---")
print(f"AUTO_CHANNEL: {AUTO_CHANNEL}")
print(f"MESSAGE_THREAD_ID: {MESSAGE_THREAD_ID}")
print(f"AUTO_INTERVAL: {AUTO_INTERVAL}")
print(f"-------------------------")
MAX_PARALLEL_MERGE = int(os.environ.get("MAX_PARALLEL", "2")) # Number of concurrent FFmpeg tasks

# Initialize state
processed_ids = set() # We will use the DB primarily, this can serve as a cache

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Bot State
class BotState:
    is_auto_running = True
    is_auto_processing = False
    is_manual_processing = False

# Initialize client
client = TelegramClient('dramabox_bot', API_ID, API_HASH)

def get_panel_buttons():
    status_text = "🟢 RUNNING" if BotState.is_auto_running else "🔴 STOPPED"
    return [
        [Button.inline("▶️ Start Auto", b"start_auto"), Button.inline("⏹ Stop Auto", b"stop_auto")],
        [Button.inline(f"📊 Status: {status_text}", b"status")]
    ]

@client.on(events.NewMessage(pattern='/update'))
async def update_bot(event):
    if event.sender_id not in ADMIN_IDS:
        return
    import subprocess
    import sys
    
    try:
        # Step 1: Fetch latest
        subprocess.run(["git", "fetch", "--all"], capture_output=True, text=True)
        # Step 2: Force Reset (Hapus file bentrok, ganti yang baru sesuai GitHub)
        result = subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, text=True)
        
        await status_msg.edit(f"✅ Repositori berhasil di-update (Hard Reset):\n```\n{result.stdout}\n```\n\nSedang memulai ulang sistem (Restarting)...")
        
        # Restart the script forcefully replacing the current process image
        os.execl(sys.executable, sys.executable, *sys.argv)
    except Exception as e:
        await status_msg.edit(f"❌ Gagal melakukan update: {e}")

@client.on(events.NewMessage(pattern='/panel'))
async def panel(event):
    if event.chat_id not in ADMIN_IDS:
        return
    await event.reply("🎛 **Dramabox Control Panel**", buttons=get_panel_buttons())

@client.on(events.CallbackQuery())
async def panel_callback(event):
    if event.sender_id not in ADMIN_IDS:
        return
        
    data = event.data
    
    try:
        if data == b"start_auto":
            BotState.is_auto_running = True
            await event.answer("Auto-mode started!")
            await event.edit("🎛 **Dramabox Control Panel**", buttons=get_panel_buttons())
        elif data == b"stop_auto":
            BotState.is_auto_running = False
            await event.answer("Auto-mode stopped!")
            await event.edit("🎛 **Dramabox Control Panel**", buttons=get_panel_buttons())
        elif data == b"status":
            await event.answer(f"Status: {'Running' if BotState.is_auto_running else 'Stopped'}")
            await event.edit("🎛 **Dramabox Control Panel**", buttons=get_panel_buttons())
    except Exception as e:
        if "message is not modified" in str(e).lower() or "Message string and reply markup" in str(e):
            pass # Ignore if button is already in that state
        else:
            logger.error(f"Callback error: {e}")

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.reply("Welcome to Dramabox Downloader Bot! 🎉\n\nGunakan perintah\n`/download {bookId}`\n`/download {title}`\n`/search {judul}`\nuntuk mulai.")

@client.on(events.NewMessage(pattern=r'/search (.+)'))
async def on_search(event):
    if event.chat_id not in ADMIN_IDS:
        return
    query = event.pattern_match.group(1)
    status_msg = await event.reply(f"🔍 Mencari `{query}`...")
    
    results = await search_dramas(query)
    if not results:
        await status_msg.edit(f"❌ Tidak ditemukan hasil untuk `{query}`.")
        return
        
    import re
    grouped_results = {}
    
    # Check top 20 to find dub vs normal pairs
    for res in results[:20]:
        title = res.get("shortPlayName") or res.get("scriptName") or res.get("book_name") or res.get("title")
        book_id = res.get("shortPlayId") or res.get("id") or res.get("book_id")
        if not title or not book_id: continue
        
        is_dub = "dub" in title.lower()
        # Clean the title perfectly to match base names
        base_title = re.sub(r'[-_\s]*\(?dub(?:bing)?\)?[-_\s]*', '', title, flags=re.IGNORECASE).strip()
        
        if base_title not in grouped_results:
            grouped_results[base_title] = {"normal": None, "dub": None}
            
        if is_dub:
            grouped_results[base_title]["dub"] = (title, book_id)
        else:
            grouped_results[base_title]["normal"] = (title, book_id)
            
    buttons = []
    # Show top 8 grouped results
    for base_title, versions in list(grouped_results.items())[:8]:
        row = []
        if versions["normal"]:
            t = versions["normal"][0][:35]
            row.append(Button.inline(f"🎬 {t}", f"dl_{versions['normal'][1]}".encode()))
            
        if versions["dub"]:
            if versions["normal"]:
                # If normal also exists on the same line, just make a small DUB button
                row.append(Button.inline(f"🎙️ DUB", f"dl_{versions['dub'][1]}".encode()))
            else:
                # If only dub exists, show full title
                t = versions["dub"][0][:35]
                row.append(Button.inline(f"🎙️ {t}", f"dl_{versions['dub'][1]}".encode()))
                
        if row:
            buttons.append(row)
            
    await status_msg.edit(f"✅ Ditemukan hasil drama untuk `{query}`.\n🎯 **Pilih versi yang ingin Anda download!**", buttons=buttons)

@client.on(events.CallbackQuery(pattern=r'^dl_(.+)'))
async def dl_callback(event):
    if event.sender_id not in ADMIN_IDS:
        return
    book_id = event.pattern_match.group(1).decode()
    
    if BotState.is_manual_processing:
        await event.answer("⚠️ Bot sedang sibuk memproses manual!", alert=True)
        return
        
    if await is_processed(book_id):
        await event.answer("⚠️ Drama ini sudah pernah diupload!", alert=True)
        return
        
    await event.answer("Mulai memproses...")
    status_msg = await client.send_message(ADMIN_ID, f"⏳ Memulai download drama ID: `{book_id}`...")
    
    BotState.is_manual_processing = True
    success = await process_drama_full(book_id, AUTO_CHANNEL, status_msg, reply_to=MESSAGE_THREAD_ID)
    # The title will be fetched inside process_drama_full, we should handle DB saving there or here.
    # Refactoring process_drama_full to return (success, title) would be better.
    BotState.is_manual_processing = False

@client.on(events.NewMessage(pattern=r'/download (.+)'))
async def on_download(event):
    chat_id = event.chat_id
    
    if chat_id not in ADMIN_IDS:
        await event.reply("❌ Maaf, perintah ini hanya untuk admin.")
        return
        
    if BotState.is_manual_processing:
        await event.reply("⚠️ Sedang memproses request manual. Tunggu hingga selesai.")
        return
        
    query = event.pattern_match.group(1)
    book_id = None
    
    # Check if it looks like an ID (long numeric string)
    if query.isdigit() and len(query) > 10:
        book_id = query
        logger.info(f"Direct ID download: {book_id}")
    else:
        # It's a title, delegate to search logic so user can choose normal/dub
        await event.reply("⚠️ Mengalihkan ke menu pencarian untuk memilih versi...")
        await on_search(event)
        return
    
    if await is_processed(book_id):
        await event.reply("⚠️ Drama ini sudah pernah diupload!")
        return
    
    # 1. Fetch data
    detail = await get_drama_detail(book_id)
    if not detail:
        await event.reply(f"❌ Gagal mendapatkan detail drama `{book_id}`.")
        return
        
    episodes = await get_all_episodes(book_id)
    if not episodes:
        await event.reply(f"❌ Drama `{book_id}` tidak memiliki episode.")
        return
    
    title = detail.get("shortPlayName") or detail.get("scriptName") or detail.get("title") or detail.get("book_name") or detail.get("name") or f"Drama_{book_id}"
    status_msg = await event.reply(f"🎬 Drama: **{title}**\n📽 Total Episodes: {len(episodes)}\n\n⏳ Sedang memproses...")
    
    BotState.is_manual_processing = True
    success = await process_drama_full(book_id, chat_id, status_msg, reply_to=MESSAGE_THREAD_ID if chat_id == AUTO_CHANNEL else None)
    BotState.is_manual_processing = False

async def process_drama_full(book_id, chat_id, status_msg=None, crf: int = 24, preset: str = "ultrafast", reply_to: int = None):
    """Refactored logic to be reusable for auto-mode and support NetShort API."""
    # 1. Fetch data with retries
    max_api_retries = 3
    detail = None
    episodes = None
    
    for i in range(max_api_retries):
        detail = await get_drama_detail(book_id)
        episodes = await get_all_episodes(book_id)
        if detail and episodes:
            break
        await asyncio.sleep(2)
    
    if not detail or not episodes:
        err_msg = f"❌ Detail atau Episode `{book_id}` tidak ditemukan."
        if status_msg: await status_msg.edit(err_msg)
        logger.error(err_msg)
        return False
        
    num_episodes = len(episodes)
    # SMART CRF: Adjust quality based on length to stay under 2GB
    if num_episodes > 70:
        crf = 27 # High compression for long dramas
    elif num_episodes > 40:
        crf = 25 # Medium compression
    else:
        crf = 23 # High quality for short dramas

    title = detail.get("shortPlayName") or detail.get("scriptName") or detail.get("title") or detail.get("book_name") or detail.get("name") or f"Drama_{book_id}"
    description = detail.get("shotIntroduce") or detail.get("intro") or detail.get("description") or "No description available."
    poster = detail.get("shortPlayCover") or detail.get("highImage") or detail.get("cover") or detail.get("poster") or ""
    
    # 1. NEW STATUS MESSAGE WITH POSTER (Static Info)
    base_info = f"🎬 **{title}**\n\n📝 `{description[:400]}`"
    status_msg = None # This will now be the PROGRESS message
    
    if poster:
        try:
            # Download poster to temp file first to ensure it sends as PHOTO
            import httpx
            import tempfile
            poster_tmp = os.path.join(tempfile.gettempdir(), f"status_poster_{book_id}.jpg")
            async with httpx.AsyncClient(verify=False) as http_client:
                resp = await http_client.get(poster)
                if resp.status_code == 200:
                    with open(poster_tmp, "wb") as f:
                        f.write(resp.content)
            
            # Send static info message
            await client.send_file(chat_id, poster_tmp, caption=base_info, reply_to=reply_to)
            if os.path.exists(poster_tmp): os.remove(poster_tmp)
        except Exception as e:
            logger.warning(f"Failed to send poster: {e}")
            await client.send_message(chat_id, base_info, reply_to=reply_to)
    
    # Send a SEPARATE message for progress
    status_msg = await client.send_message(chat_id, "⏳ **Memulai pemrosesan...**", reply_to=reply_to)
    
    # 2. Setup temp directory
    temp_dir = tempfile.mkdtemp(prefix=f"netshort_{book_id}_")
    video_dir = os.path.join(temp_dir, "episodes")
    os.makedirs(video_dir, exist_ok=True)
    
    try:
        # 3. Download (Now requires book_id)
        async def download_progress_cb(downloaded_count, total_count):
            if not status_msg: return
            pct = downloaded_count / total_count if total_count > 0 else 0
            filled = int(pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            pct_str = int(pct * 100)
            text = (
                f"📥 Status: Downloading Episodes...\n"
                f"🎬 Episode {downloaded_count}/{total_count}\n"
                f"|{bar}| {pct_str}%"
            )
            try:
                await status_msg.edit(text)
            except Exception as e:
                logger.debug(f"Progress update failed: {e}")
            
        download_success, success_count, total_count = await download_all_episodes(
            book_id, episodes, video_dir, progress_callback=download_progress_cb
        )
        
        # IMPROVEMENT: If at least 90% is downloaded, we can try to proceed
        if success_count < total_count:
            if success_count > (total_count * 0.9):
                logger.warning(f"⚠️ Only {success_count}/{total_count} episodes downloaded. Proceeding with partial drama...")
                if status_msg: await status_msg.edit(f"⚠️ Warning: Hanya {success_count}/{total_count} episode berhasil diunduh. Melanjutkan penggabungan...")
            else:
                err_msg = f"❌ Download Gagal: **{title}** ({success_count}/{total_count} eps)"
                if status_msg: await status_msg.edit(err_msg)
                logger.error(err_msg)
                return False

        # 4. Merge (supports per-episode processing)
        if status_msg: await status_msg.edit(f"📽 Merging {success_count}/{total_count} episodes...")
        safe_title = sanitize_filename(title)
        output_video_path = os.path.join(temp_dir, f"{safe_title}.mp4")
        
        async def merge_progress_cb(pct, cep, teps, em, es):
            if not status_msg: return
            filled = int(pct * 10)
            bar = "█" * filled + "░" * (10 - filled)
            pct_str = int(pct * 100)
            
            # Dynamically change status text
            status_title = "🔥 Status: Burning Hardsub..."
            if cep == teps and pct > 0.95:
                status_title = "📽 Status: Final Merging (Concat)..."
                
            text = (
                f"{status_title}\n"
                f"🎬 Episode {cep}/{teps}\n"
                f"|{bar}| {pct_str}%\n"
                f"⏳ {('Selesai dalam: ' + str(em) + 'm ' + str(es) + 's') if em > 0 or es > 0 else 'Hampir selesai...'}"
            )
            try:
                await status_msg.edit(text)
            except Exception as e:
                logger.debug(f"Merge progress update failed: {e}")
                
        # The merger will automatically hardsub if it finds per-episode subtitles!
        merge_success = await merge_episodes(
            video_dir, output_video_path, 
            crf=crf, preset=preset, 
            max_parallel=MAX_PARALLEL_MERGE,
            progress_callback=merge_progress_cb
        )
        
        if not merge_success:
            err_msg = f"❌ **Merge Gagal (FFmpeg Error)**: **{title}**\n\nSilakan periksa log terminal untuk detail teknis (biasanya terkait font atau path subtitle)."
            if status_msg: await status_msg.edit(err_msg)
            logger.error(err_msg)
            return False

        # 5. Check Size & Split if needed (> 1.9GB)
        video_size = os.path.getsize(output_video_path)
        upload_queue = [output_video_path]
        
        if video_size > 1900 * 1024 * 1024:
            if status_msg: await status_msg.edit(f"✂️ Size {video_size/(1024*1024*1024):.2f}GB exceeds limit. Splitting into 2 parts...")
            upload_queue = await split_video(output_video_path, temp_dir)

        # 6. Upload
        success_count_upload = 0
        for i, v_path in enumerate(upload_queue):
            part_info = f" Part {i+1}/{len(upload_queue)}" if len(upload_queue) > 1 else ""
            if status_msg: await status_msg.edit(f"📤 Uploading **{title}**{part_info}...")
            
            res = await upload_drama(
                client, chat_id, 
                title + part_info, description, 
                poster if i == 0 else "", # Only send poster once
                v_path,
                ep_info=f"{success_count}/{total_count}",
                reply_to=reply_to
            )
            if res: success_count_upload += 1

        if success_count_upload == len(upload_queue):
            if status_msg: 
                try: await status_msg.delete()
                except: pass
            # Save to database upon success
            await save_processed_db(book_id, title)
            processed_ids.add(book_id)
            return True
        else:
            err_msg = f"❌ Upload Gagal (Sebagian atau Semua): **{title}**"
            if status_msg: await status_msg.edit(err_msg)
            logger.error(err_msg)
            return False
            
    except Exception as e:
        logger.error(f"Error processing {book_id}: {e}")
        if status_msg: await status_msg.edit(f"❌ Error: {e}")
        return False
    finally:
        if os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except: pass

async def auto_mode_loop():
    """Loop to find and process new dramas automatically using NetShort feed."""
    global processed_ids
    
    logger.info("🚀 NetShort Auto-Mode Started.")
    
    # 0. Resolve Entity (Telethon needs to "see" the channel at least once)
    try:
        logger.info(f"Checking access to channel: {AUTO_CHANNEL}...")
        entity = await client.get_entity(AUTO_CHANNEL)
        logger.info(f"✅ Access confirmed to: {getattr(entity, 'title', 'Unknown Title')}")
    except Exception as e:
        logger.error(f"❌ Failed to reach AUTO_CHANNEL ({AUTO_CHANNEL}): {e}")
        logger.warning("Make sure the bot is an ADMIN in the channel/group/topic.")

    is_initial_run = True
    
    while True:
        if not BotState.is_auto_running:
            await asyncio.sleep(5)
            continue
            
        try:
            # For NetShort, pages start from 1
            logger.info("🔍 Scanning for new dramas...")
            
            # Fetch from home or list/1
            new_dramas = await get_latest_dramas(pages=2 if is_initial_run else 1, page_start=1) or []
            
            # Map items and filter processed
            queue = []
            for d in new_dramas:
                bid = str(d.get("shortPlayId") or d.get("id") or d.get("book_id") or "")
                if not bid: continue
                
                # Check cache first, then DB
                if bid in processed_ids:
                    continue
                
                if await is_processed(bid):
                    processed_ids.add(bid)
                    continue
                    
                queue.append(d)
            
            new_found = 0
            for drama in queue:
                if not BotState.is_auto_running:
                    break
                    
                book_id = str(drama.get("shortPlayId") or drama.get("id") or drama.get("book_id") or "")
                title = drama.get("shortPlayName") or drama.get("scriptName") or drama.get("book_name") or drama.get("title") or "Unknown"
                
                logger.info(f"✨ [NETSHORT] New drama: {title} ({book_id}). Starting process...")
                new_found += 1
                
                # Notify admin
                status_msg = None
                try:
                    status_msg = await client.send_message(ADMIN_ID, f"🆕 **NetShort Auto-System Mendeteksi Drama Baru!**\n🎬 {title}\n🆔 `{book_id}`\n⏳ Memproses...")
                except: pass
                
                BotState.is_auto_processing = True
                success = await process_drama_full(book_id, AUTO_CHANNEL, status_msg, reply_to=MESSAGE_THREAD_ID)
                BotState.is_auto_processing = False
                
                if success:
                    logger.info(f"✅ Finished {title}")
                    # Note: save_processed_db is already called inside process_drama_full
                    try:
                        await client.send_message(ADMIN_ID, f"✅ Sukses Auto-Post: **{title}**")
                    except: pass
                else:
                    logger.error(f"❌ Failed to process {title}")
                    try:
                        await client.send_message(ADMIN_ID, f"🚨 **ERROR**: Gagal memproses `{title}`.")
                    except: pass
                
                await asyncio.sleep(10)
            
            if new_found == 0 and not is_initial_run:
                logger.info("😴 No new dramas found.")
            
            is_initial_run = False
            # Wait based on AUTO_INTERVAL
            for _ in range(AUTO_INTERVAL):
                if not BotState.is_auto_running: break
                await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"⚠️ Error in auto_mode_loop: {e}")
            await asyncio.sleep(60)


if __name__ == '__main__':
    logger.info("Initializing Dramabox Auto-Bot...")
    
    async def main():
        # 1. Init DB
        await init_db()
        
        # 2. Start auto loop
        client.loop.create_task(auto_mode_loop())
        
        logger.info("Bot is active and monitoring.")
        await client.run_until_disconnected()

    with client:
        client.loop.run_until_complete(main())
