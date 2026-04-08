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

from api import get_video_and_sub

async def download_all_episodes(drama_id, episodes, download_dir: str, semaphore_count: int = 5, progress_callback=None):
    """
    Downloads all episodes concurrently.
    drama_id: ID of the drama
    episodes: list of dicts with 'episode' or 'ep'
    """
    os.makedirs(download_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(semaphore_count)

    tasks = []
    
    total_episodes = len(episodes)
    completed_count = 0
    
    async def limited_download(ep):
        async with semaphore:
            # Ep can be a dict {'episode': 1} or just the episode number if it's a list?
            # NetShort usually provides a list of objects with 'episode'.
            ep_num_val = ep.get('episode') or ep.get('ep') or ep.get('id')
            if ep_num_val is None:
                logger.error(f"Could not determine episode number from {ep}")
                return False
                
            ep_num_str = str(ep_num_val).zfill(3)
            filename = f"episode_{ep_num_str}.mp4"
            filepath = os.path.join(download_dir, filename)
            
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Fetch URL using drama_id and episode number
                    vid_url, sub_url = await get_video_and_sub(drama_id, int(ep_num_val))
                    if not vid_url:
                        logger.error(f"No Video URL found for Drama {drama_id} EP {ep_num_val} - Attempt {attempt+1}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        return False
                        
                    async with httpx.AsyncClient(timeout=120, verify=False) as client:
                        success = await download_file(client, vid_url, filepath)
                        
                        # Download subtitle if available
                        if success and sub_url:
                            sub_ext = ".srt" if ".srt" in sub_url.lower() else ".ass" if ".ass" in sub_url.lower() else ".srt"
                            sub_filepath = os.path.join(download_dir, f"episode_{ep_num_str}{sub_ext}")
                            try:
                                await download_file(client, sub_url, sub_filepath)
                            except Exception as e:
                                logger.warning(f"Failed to download sub for EP {ep_num_val}: {e}")

                        if success:
                            # Verify file size (sometimes it's a tiny HTML error page instead of video)
                            if os.path.exists(filepath) and os.path.getsize(filepath) > 100000: # >100KB
                                logger.info(f"Downloaded {filename}")
                                nonlocal completed_count
                                completed_count += 1
                                if progress_callback:
                                    try:
                                        await progress_callback(completed_count, total_episodes)
                                    except: pass
                                return True
                            else:
                                logger.warning(f"File {filename} is too small, likely corrupted - Attempt {attempt+1}")
                except Exception as e:
                    logger.error(f"Error downloading {filename} - Attempt {attempt+1}: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
            
            return False

    results = await asyncio.gather(*(limited_download(ep) for ep in episodes))
    
    success = all(results)
    success_count = sum(1 for r in results if r is True)
    total_count = len(episodes)
    
    if success:
        logger.info(f"✅ All {total_count} episodes downloaded successfully from NetShort.")
    else:
        logger.error(f"❌ Failed to download some episodes ({success_count}/{total_count}). Complete download is required.")
        
    return success, success_count, total_count
