import httpx
import logging

logger = logging.getLogger(__name__)

# NetShort API Configuration
BASE_URL = "https://netshort.dramabos.my.id/api"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

async def get_latest_dramas(pages=1, page_start=1):
    """Fetches trending dramas from NetShort API home/list section."""
    all_dramas = []
    
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        for p in range(page_start, page_start + pages):
            url = f"{BASE_URL}/home/{p}"
            params = {"lang": "in"}
            try:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    # Broad search for list of dramas in the response
                    found_in_page = []
                    
                    # Try common containers
                    if "data" in data:
                        res_data = data["data"]
                        if isinstance(res_data, list):
                            found_in_page = res_data
                        elif isinstance(res_data, dict):
                            # Try known kyes for NetShort/Melolo variant structures
                            found_in_page = (
                                res_data.get("list", []) or 
                                res_data.get("cell", {}).get("cell_data", []) or
                                res_data.get("searchCodeSearchResult", []) or
                                res_data.get("books", [])
                            )
                            # If it's a dict with another dict inside, look for list
                            if not found_in_page:
                                for k, v in res_data.items():
                                    if isinstance(v, list) and v:
                                        found_in_page = v
                                        break
                                        
                    if not found_in_page:
                        # Continue to next page rather than breaking if possible
                        continue
                    
                    all_dramas.extend(found_in_page)
                else:
                    break
            except Exception as e:
                logger.error(f"Error fetching home page {p}: {e}")
                break
    
    return all_dramas

async def get_drama_list(page=1, region="", audio="", tag="", sort=""):
    """Fetches drama list with filters."""
    url = f"{BASE_URL}/list/{page}"
    params = {
        "lang": "in",
        "Region": region,
        "Audio": audio,
        "Tag": tag,
        "Sort": sort
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                res_data = data.get("data", [])
                if isinstance(res_data, dict):
                    return res_data.get("list", []) or res_data.get("searchCodeSearchResult", [])
                return res_data
            return []
        except Exception as e:
            logger.error(f"Error fetching list page {page}: {e}")
            return []

async def get_categories(lang="in"):
    """Fetches categories from NetShort API."""
    url = f"{BASE_URL}/categories"
    params = {"lang": lang}
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data and "data" in data:
                return data["data"]
            return data
        except Exception as e:
            logger.error(f"Error fetching categories: {e}")
            return []

async def get_drama_detail(drama_id: str):
    """Fetches drama detail from NetShort API."""
    url = f"{BASE_URL}/drama/{drama_id}"
    params = {"lang": "in"}
    
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data:
                # Some endpoints root data, others nest in 'data'
                res_data = data.get("data") or data
                # NetShort sometimes nests it another level in 'detail'
                if isinstance(res_data, dict) and "detail" in res_data:
                    return res_data["detail"]
                return res_data
            return None
        except Exception as e:
            logger.error(f"Error fetching drama detail for {drama_id}: {e}")
            return None

async def get_all_episodes(drama_id: str):
    """Fetches episodes list from drama detail."""
    detail = await get_drama_detail(drama_id)
    if not detail:
        return []
    
    # Check for 'videos', 'episodes', 'list', or 'chapterList' (NetShort variant)
    episodes = (
        detail.get("chapterList") or
        detail.get("videos") or 
        detail.get("episodes") or 
        detail.get("list", [])
    )
    
    # If API provides totalEpisode but no explicit list, we mock the list for the downloader
    if not episodes and detail.get("totalEpisode"):
        total = int(detail["totalEpisode"])
        episodes = [{"episode": i} for i in range(1, total + 1)]
        
    return episodes

async def search_dramas(query: str, page=1):
    """Searches dramas by title."""
    url = f"{BASE_URL}/search"
    params = {
        "lang": "in",
        "q": query,
        "page": page
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data and "data" in data:
                res_data = data["data"]
                if isinstance(res_data, dict):
                    # Found key: searchCodeSearchResult
                    return res_data.get("searchCodeSearchResult", []) or res_data.get("list", [])
                return res_data
            return []
        except Exception as e:
            logger.error(f"Error searching for {query}: {e}")
            return []

async def get_video_url(drama_id: str, ep: int):
    """Fetches the actual play URL for a specific episode."""
    url = f"{BASE_URL}/watch/{drama_id}/{ep}"
    params = {
        "lang": "in",
        "code": AUTH_CODE
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            # Response usually has 'url' or 'backup' or 'videoUrl' in 'data'
            result = data.get("data") or data
            if isinstance(result, dict):
                return result.get("videoUrl") or result.get("url") or result.get("backup")
            return None
        except Exception as e:
            logger.error(f"Error fetching video URL for {drama_id} EP {ep}: {e}")
            return None

async def get_video_and_sub(drama_id: str, ep: int):
    """Fetches both video URL and subtitle URL from a specific episode if available."""
    url = f"{BASE_URL}/watch/{drama_id}/{ep}"
    params = {
        "lang": "in",
        "code": AUTH_CODE
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            result = data.get("data") or data
            if isinstance(result, dict):
                vid_url = result.get("videoUrl") or result.get("url") or result.get("backup")
                sub_url = None
                if "subtitles" in result:
                    subs = result["subtitles"]
                    if isinstance(subs, list) and len(subs) > 0:
                        for sub in subs:
                            if sub.get("lang") == "id_ID":
                                sub_url = sub.get("url")
                                break
                        if not sub_url:
                            sub_url = subs[0].get("url")
                return vid_url, sub_url
            return None, None
        except Exception as e:
            logger.error(f"Error fetching watch data for {drama_id}: {e}")
            return None, None

async def get_subtitle_url(drama_id: str, ep: int = 1):
    """Fetches the subtitle URL from a specific episode if available."""
    url = f"{BASE_URL}/watch/{drama_id}/{ep}"
    params = {
        "lang": "in",
        "code": AUTH_CODE
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            result = data.get("data") or data
            if isinstance(result, dict) and "subtitles" in result:
                subs = result["subtitles"]
                if isinstance(subs, list) and len(subs) > 0:
                    # Look for Indonesian sub if multiple exist, else return the first one
                    for sub in subs:
                        if sub.get("lang") == "id_ID":
                            return sub.get("url")
                    return subs[0].get("url")
            return None
        except Exception as e:
            logger.error(f"Error fetching subtitle URL for {drama_id}: {e}")
            return None

# Backward compatibility aliases
async def get_latest_idramas(pages=1):
    return await get_latest_dramas(pages=pages)

async def get_idrama_detail(drama_id: str):
    return await get_drama_detail(drama_id)

async def get_idrama_all_episodes(drama_id: str):
    return await get_all_episodes(drama_id)

