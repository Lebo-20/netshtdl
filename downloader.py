import os
import asyncio
import httpx
import logging

logger = logging.getLogger(__name__)

async def download_file(client, url: str, path: str, progress_callback=None):
    """Downloads a single file using aria2c with a robust httpx fallback."""
    try:
        dir_name = os.path.dirname(path)
        file_name = os.path.basename(path)
        
        # 1. ATTEMPT WITH ARIA2C
        headers_aria = [
            f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            f"--header=Referer: https://netshort.com/",
            f"--header=Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5",
            f"--header=Accept-Language: en-US,en;q=0.9,id;q=0.8",
        ]

        cmd = [
            "aria2c",
            "-x", "16", "-s", "16", "-j", "16", "-k", "1M",
            "--continue=true", "--auto-file-renaming=false", "--allow-overwrite=true",
            "--console-log-level=error", "--connect-timeout=30", "--timeout=30",
            *headers_aria,
            "-d", dir_name,
            "-o", file_name,
            url
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 100000:
            return True
            
        # 2. FALLBACK TO ROBUST HTTPX
        err_msg = stderr.decode(errors='ignore').strip()
        logger.warning(f"Aria2c failed or produced empty file. Falling back to HTTPX for: {file_name}\nError: {err_msg}")
        
        return await download_with_httpx(url, path)
            
    except Exception as e:
        logger.error(f"Critical error in download_file: {e}")
        return False

async def download_with_httpx(url, path):
    """Native Python download with validation and retry logic."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://netshort.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,video/mp4,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    try:
        async with httpx.AsyncClient(timeout=600, verify=False, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                # VALIDASI 1: Cek status code
                if response.status_code != 200:
                    logger.error(f"HTTP Error {response.status_code} for {url}")
                    return False
                
                # VALIDASI 2: Cek Content-Type
                content_type = response.headers.get("Content-Type", "").lower()
                if "video" not in content_type and "application/octet-stream" not in content_type:
                    logger.error(f"Invalid Content-Type: {content_type}. Expected video/mp4.")
                    if "text/html" in content_type or "application/json" in content_type:
                        return False
                
                # Mulai download ke file sementara
                temp_path = path + ".tmp"
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024*1024):
                        if chunk: f.write(chunk)
                
                # VALIDASI 3: Cek ukuran file minimal
                if os.path.getsize(temp_path) < 100000:
                    logger.error(f"Downloaded file too small: {os.path.getsize(temp_path)} bytes")
                    if os.path.exists(temp_path): os.remove(temp_path)
                    return False
                
                # Sukses, rename file
                if os.path.exists(path): os.remove(path)
                os.rename(temp_path, path)
                logger.info(f"Successfully downloaded with HTTPX: {os.path.basename(path)}")
                return True
                
    except Exception as e:
        logger.error(f"HTTPX Download Exception: {e}")
        if os.path.exists(path + ".tmp"):
            try: os.remove(path + ".tmp")
            except: pass
        return False

from api import get_video_and_sub

async def download_all_episodes(drama_id, episodes, download_dir: str, semaphore_count: int = 5, progress_callback=None):
    """Downloads all episodes concurrently."""
    os.makedirs(download_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(semaphore_count)

    total_episodes = len(episodes)
    completed_count = 0
    
    async def limited_download(ep):
        async with semaphore:
            ep_num_val = ep.get('episode') or ep.get('ep') or ep.get('id')
            if ep_num_val is None:
                return False
                
            ep_num_str = str(ep_num_val).zfill(3)
            filename = f"episode_{ep_num_str}.mp4"
            filepath = os.path.join(download_dir, filename)
            
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    await asyncio.sleep(0.5)
                    vid_url, sub_url = await get_video_and_sub(drama_id, int(ep_num_val))
                    if not vid_url:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(5)
                            continue
                        return False
                        
                    async with httpx.AsyncClient(timeout=120, verify=False) as client:
                        # download_file now handles internal fallback to httpx
                        success = await download_file(client, vid_url, filepath)
                        
                        if success and sub_url:
                            sub_ext = ".srt" if ".srt" in sub_url.lower() else ".ass" if ".ass" in sub_url.lower() else ".srt"
                            sub_filepath = os.path.join(download_dir, f"episode_{ep_num_str}{sub_ext}")
                            try:
                                await download_with_httpx(sub_url, sub_filepath)
                            except: pass

                        if success:
                            nonlocal completed_count
                            completed_count += 1
                            if progress_callback:
                                try: await progress_callback(completed_count, total_episodes)
                                except: pass
                            return True
                except Exception as e:
                    logger.error(f"Error EP {ep_num_val}: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
            return False

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    
    success_count = sum(1 for r in results if r is True)
    total_count = len(episodes)
    
    return success_count == total_count, success_count, total_count
