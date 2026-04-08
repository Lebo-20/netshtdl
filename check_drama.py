import asyncio
from api import get_all_episodes, get_video_url

async def check():
    drama_id = "2042781442030275586" # Replace with actual if I can see it
    # I'll search for "Hari Pembalasan" again to be sure
    from api import search_dramas
    res = await search_dramas("Hari Pembalasan")
    if not res: 
        print("Not found")
        return
    drama_id = res[0].get("shortPlayId") or res[0].get("id")
    print(f"Checking ID: {drama_id}")
    
    eps = await get_all_episodes(drama_id)
    print(f"Episodes: {len(eps)}")
    if eps:
        v_url = await get_video_url(drama_id, eps[0].get("episode", 1))
        print(f"Video URL 1: {v_url}")

asyncio.run(check())
