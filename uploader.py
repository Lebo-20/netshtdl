import os
import asyncio
import re
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo
import logging

logger = logging.getLogger(__name__)

def sanitize_filename(filename: str):
    """Removes invalid characters for Windows/Linux file systems."""
    return re.sub(r'[\\/*?:"<>|]', "", filename)

async def upload_progress(current, total, event, msg_text="Uploading..."):
    """Callback function for upload progress."""
    percentage = (current / total) * 100
    try:
        # Avoid flood by updating every few percentages
        if int(percentage) % 10 == 0:
            await event.edit(f"{msg_text} {percentage:.1f}%")
    except:
        pass

async def upload_drama(client: TelegramClient, chat_id: int, 
                       title: str, description: str, 
                       poster_url: str, video_path: str):
    """
    Uploads the drama information and merged video to Telegram.
    """
    import subprocess
    import tempfile
    
    try:
        # 1. Send Poster + Description as PHOTO
        clean_desc = description[:800] if description else "No description."
        caption = f"🎬 **{title}**\n\n📝 **Sinopsis:**\n{clean_desc}..."
        
        poster_path = None
        if poster_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30) as http_client:
                    resp = await http_client.get(poster_url)
                    if resp.status_code == 200:
                        poster_path = os.path.join(tempfile.gettempdir(), f"poster_{hash(title)}.jpg")
                        with open(poster_path, "wb") as pf:
                            pf.write(resp.content)
            except Exception as e:
                logger.warning(f"Failed to download poster: {e}")

        # Send as visible photo (if possible)
        try:
            if poster_path or (poster_url and poster_url.startswith("http")):
                await client.send_file(
                    chat_id,
                    poster_path or poster_url,
                    caption=caption,
                    parse_mode='md',
                    force_document=False
                )
            else:
                # Fallback to message if no poster
                await client.send_message(chat_id, caption, parse_mode='md')
        except Exception as e:
            logger.warning(f"Failed to send poster/caption: {e}")
            # Try to send just the caption as text if file fails
            try:
                await client.send_message(chat_id, caption, parse_mode='md')
            except: pass
        
        # Cleanup poster temp file
        if poster_path and os.path.exists(poster_path):
            try: os.remove(poster_path)
            except: pass
        
        status_msg = await client.send_message(chat_id, f"📤 Ekstraksi info & upload video: **{title}**...")
        
        # 2. Extract Duration & Dimensions
        duration = 0
        width = 0
        height = 0
        try:
            ffprobe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
            output_raw = subprocess.check_output(ffprobe_cmd, text=True).strip()
            if output_raw:
                output = output_raw.split('\n')
                if len(output) >= 3:
                    width = int(output[0])
                    height = int(output[1])
                    duration = int(float(output[2]))
        except Exception as e:
            logger.warning(f"Failed to extract video info: {e}")

        # 3. Extract Thumbnail
        thumb_path = os.path.join(tempfile.gettempdir(), f"thumb_{hash(video_path)}.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01.000", "-vframes", "1", thumb_path], capture_output=True)
            if not os.path.exists(thumb_path):
                thumb_path = None
        except Exception as e:
            logger.warning(f"Failed to generate thumbnail: {e}")
            thumb_path = None

        # 4. Upload Video
        from telethon.tl.types import DocumentAttributeVideo
        video_attributes = [
            DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True
            )
        ]
        
        await client.send_file(
            chat_id,
            video_path,
            caption=f"🎥 Full Episode: **{title}**",
            force_document=False,
            thumb=thumb_path,
            attributes=video_attributes,
            progress_callback=lambda c, t: upload_progress(c, t, status_msg, "Upload Video:"),
            supports_streaming=True
        )
        
        try: await status_msg.delete()
        except: pass
        
        if thumb_path and os.path.exists(thumb_path):
            try: os.remove(thumb_path)
            except: pass
            
        logger.info(f"Successfully uploaded {title} to Telegram")
        return True
    except Exception as e:
        logger.error(f"Failed to upload to Telegram: {e}")
        return False
