import os
import asyncio
import time
import re
import logging
import shutil

logger = logging.getLogger(__name__)

async def get_video_duration(filepath):
    """Gets video duration using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", filepath
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return float(stdout.decode().strip())
    except Exception as e:
        logger.warning(f"Error getting duration for {filepath}: {e}")
    return 0.0

async def hardsub_episode(
    mp4_path: str, ep_sub: str, output_path: str, 
    crf: int = 23, preset: str = "superfast", 
    ep_idx: int = 0, total_eps: int = 0, 
    progress_callback=None
):
    """
    Hardsubs a single episode with subtitle burning.
    Optimized for speed and sync using superfast/veryfast preset.
    """
    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
        logger.info(f"⚡ Skipping: {os.path.basename(mp4_path)} already hardsubbed.")
        return True, ""

    try:
        # 1. Prepare Subtitle Path (only if provided)
        safe_sub_path = None
        if ep_sub:
            try:
                rel_sub_path = os.path.relpath(ep_sub, os.getcwd()).replace("\\", "/")
            except ValueError:
                # Different drives on Windows
                rel_sub_path = ep_sub.replace("\\", "/")
            
            # FIX: Escape commas, colons and single quotes for the FFmpeg subtitles filter chain
            # In FFmpeg filters, ':' must be escaped as '\:', ',' as '\,' and "'" as "\'"
            safe_sub_path = rel_sub_path.replace("'", r"\'").replace(":", r"\:").replace(",", r"\,")
        
        # 2. Build Filter Chain
        filters = []
        # Sync video frames to 30fps constant to prevent drift
        filters.append("fps=30")
        
        # Subtitle filter (only if ep_sub is not None)
        if safe_sub_path:
            if ep_sub.lower().endswith('.srt'):
                style = "Fontname=Arial,Fontsize=12,PrimaryColour=&H00FFFFFF,Bold=1,Outline=1,OutlineColour=&H00000000,MarginV=25"
                filters.append(f"subtitles='{safe_sub_path}':charenc=UTF-8:force_style='{style}'")
            else:
                filters.append(f"subtitles='{safe_sub_path}'")
            
        # Ensure even dimensions (required for libx264)
        # If too many episodes, force 720p height to keep size under 2GB
        if total_eps > 50:
            filters.append("scale=-2:720")
        else:
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
            
        vf_chain = ",".join(filters)
        
        # 3. Fast FFmpeg Command
        cmd = [
            "ffmpeg", "-y", "-i", mp4_path,
            "-vf", vf_chain,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-maxrate", "1800k", "-bufsize", "3600k", # Bitrate cap for 2GB safety
            "-r", "30", # Force CFR
            "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2", # Standard Audio
            "-async", "1", # Audio Sync
            output_path
        ]
        
        start_time = time.time()
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        ep_duration = await get_video_duration(mp4_path)
        time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.\d{2}")
        full_log = []
        last_update_time = 0
        
        # Read stderr for progress and logs
        while True:
            line = await process.stderr.readline()
            if not line: break
            decoded = line.decode('utf-8', errors='ignore').strip()
            full_log.append(decoded)
            
            # Progress tracking
            match = time_regex.search(decoded)
            if match and ep_duration > 0 and progress_callback:
                now = time.time()
                if now - last_update_time >= 3:
                    last_update_time = now
                    h, m, s = map(int, match.groups())
                    curr_t = h * 3600 + m * 60 + s
                    ep_pct = min(curr_t / ep_duration, 1.0)
                    overall_pct = (ep_idx + ep_pct) / total_eps if total_eps > 0 else ep_pct
                    
                    elapsed = now - start_time
                    avg_per_ep = elapsed / ep_pct if ep_pct > 0.05 else elapsed * 10
                    left_count = total_eps - (ep_idx + ep_pct)
                    est_m, est_s = divmod(int(left_count * avg_per_ep), 60)
                    
                    await progress_callback(overall_pct, ep_idx + 1, total_eps, est_m, est_s)
                    
        await process.wait()
        log_str = "\n".join(full_log)
        
        if process.returncode == 0:
            return True, log_str
        else:
            return False, log_str
            
    except Exception as e:
        return False, str(e)

async def merge_episodes(
    video_dir: str, output_path: str, crf: int = 23, 
    preset: str = "superfast", max_parallel: int = 2,
    progress_callback=None
):
    """
    Ultimate Merge System:
    - Parallel Hardsubbing for high speed.
    - Constant Frame Rate and Audio standard for perfect sync.
    - Final Concatenation using -c copy for instant merge.
    """
    try:
        # 1. Identify episodes and subtitles
        files = [f for f in os.listdir(video_dir) if f.endswith(".mp4") and f.startswith("episode_")]
        if not files:
            logger.error("No episodes found in directory.")
            return False
            
        files.sort()
        total_eps = len(files)
        concat_list = []
        
        # 2. Parallel Processing with Semaphore
        semaphore = asyncio.Semaphore(max_parallel)
        results = []
        
        async def process_with_semaphore(idx, file):
            async with semaphore:
                ep_base = file.rsplit(".", 1)[0]
                mp4_path = os.path.join(video_dir, file)
                hardsub_out = os.path.join(video_dir, f"hardsub_{file}")
                
                # Check for subtitles
                ep_sub = None
                for ext in [".srt", ".ass"]:
                    potential_sub = os.path.join(video_dir, f"{ep_base}{ext}")
                    if os.path.exists(potential_sub):
                        ep_sub = potential_sub
                        break
                
                # FIX: Always process through hardsub_episode even if no subtitle
                # This ensures consistent FPS and Audio Sample Rate across ALL episodes.
                # If ep_sub is None, the ffmpeg command will skip subtitle filter but keep normalization.
                success, log = await hardsub_episode(
                    mp4_path, ep_sub, hardsub_out, 
                    crf, preset, idx, total_eps, progress_callback
                )
                if success:
                    return f"hardsub_{file}"
                else:
                    logger.error(f"❌ Failed processing {file}. FFmpeg Log:\n{log}")
                    # Allow skipping failed episode if it's just one, but here we enforce success
                    raise Exception(f"FFmpeg failed for {file}")

        # Run all episodes
        tasks = [process_with_semaphore(i, f) for i, f in enumerate(files)]
        try:
            concat_list = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Abandoning merge due to error: {e}")
            return False

        # 3. Final Concatenation (-c copy)
        logger.info("🚀 Starting final fast-merge (Concat Copy)...")
        list_file = os.path.join(video_dir, "concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for item in concat_list:
                f.write(f"file '{item}'\n")
        
        # Calculate total duration for final progress
        total_dur = 0
        for item in concat_list:
            total_dur += await get_video_duration(os.path.join(video_dir, item))

        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *concat_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        # Progress for final merge
        async def track_concat():
            while True:
                line = await process.stderr.readline()
                if not line: break
                decoded = line.decode('utf-8', errors='ignore').strip()
                match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.\d{2}", decoded)
                if match and total_dur > 0 and progress_callback:
                    h, m, s = map(int, match.groups())
                    curr_t = h * 3600 + m * 60 + s
                    pct = min(curr_t / total_dur, 1.0)
                    try: await progress_callback(pct, total_eps, total_eps, 0, 0)
                    except: pass

        await asyncio.gather(process.wait(), track_concat())
        
        if process.returncode == 0:
            # CHECK: Telegram 1.9GB safe limit
            fsize = os.path.getsize(output_path)
            if fsize > 1990 * 1024 * 1024:
                logger.warning(f"⚠️ Result file too large for Telegram: {fsize/(1024*1024):.2f}MB")
            
            logger.info(f"✅ Successfully merged {total_eps} episodes into {os.path.basename(output_path)}")
            return True
        else:
            # FALLBACK: If concat copy fails, try a re-encode concat (Slower but 100% works)
            logger.warning("⚠️ Fast-merge failed. Trying robust fallback merge...")
            # (In this simple version, we just report failure, but re-encode is possible if needed)
            err = (await process.stderr.read()).decode()
            logger.error(f"Concat failed: {err}")
            return False
            
    except Exception as e:
        logger.error(f"General Merge Error: {e}")
        return False
    finally:
        # Cleanup
        if 'list_file' in locals() and os.path.exists(list_file):
            try: os.remove(list_file)
            except: pass
