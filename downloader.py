import os
import asyncio
import httpx
import logging

logger = logging.getLogger(__name__)

async def download_file(client: httpx.AsyncClient, url: str, path: str, progress_callback=None):
    """Downloads a single file with potential progress tracking."""
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            
            total_size = int(response.headers.get("Content-Length", 0))
            download_size = 0
            
            with open(path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
                    download_size += len(chunk)
                    if progress_callback:
                        await progress_callback(download_size, total_size)
        return True
    except Exception as e:
        logger.error(f"Failed to download {url}: {e}")
        return False

from api import get_video_url

async def download_all_episodes(episodes, download_dir: str, semaphore_count: int = 5):
    """
    Downloads all episodes concurrently.
    episodes: list of dicts with 'episode' and 'vid' for Melolo API
    """
    os.makedirs(download_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(semaphore_count)

    tasks = []
    
    async def limited_download(ep):
        async with semaphore:
            ep_num = str(ep.get('episode', 'unk')).zfill(3)
            filename = f"episode_{ep_num}.mp4"
            filepath = os.path.join(download_dir, filename)
            
            # If already exists (maybe from previous attempt), skip?
            # Actually better to redownload to be safe if we are retrying the whole drama
            
            vid = ep.get('vid')
            if not vid:
                logger.error(f"No Video ID found for episode {ep_num}")
                return False
                
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Fetch URL from vid (fresh URL for each attempt since they might expire)
                    url = await get_video_url(vid)
                    if not url:
                        logger.error(f"No URL found for vid {vid} (Episode {ep_num}) - Attempt {attempt+1}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        return False
                        
                    async with httpx.AsyncClient(timeout=120) as client:
                        success = await download_file(client, url, filepath)
                        if success:
                            # Verify file size (sometimes it's a tiny HTML error page instead of video)
                            if os.path.exists(filepath) and os.path.getsize(filepath) > 100000: # >100KB
                                logger.info(f"Downloaded {filename}")
                                return True
                            else:
                                logger.warning(f"File {filename} is too small, likely corrupted - Attempt {attempt+1}")
                except Exception as e:
                    logger.error(f"Error downloading {filename} - Attempt {attempt+1}: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
            
            return False

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    return all(results)
