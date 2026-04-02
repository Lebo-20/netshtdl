import httpx
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://melolo.dramabos.my.id/api"
AUTH_CODE = "A8D6AB170F7B89F2182561D3B32F390D"

async def get_latest_dramas(pages=1, offset=0):
    """Fetches trending dramas from Melolo API home section."""
    all_dramas = []
    
    async with httpx.AsyncClient(timeout=30) as client:
        current_offset = offset
        for p in range(pages):
            url = f"{BASE_URL}/home"
            params = {
                "lang": "id",
                "offset": current_offset
            }
            try:
                response = await client.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    # Melolo structure: data.cell.cell_data -> list of sections -> each has 'books'
                    cell_data = data.get("data", {}).get("cell", {}).get("cell_data", [])
                    if not cell_data:
                        break
                    
                    found_in_page = []
                    for section in cell_data:
                        books = section.get("books", [])
                        found_in_page.extend(books)
                    
                    if not found_in_page:
                        break
                        
                    all_dramas.extend(found_in_page)
                    # Use next_offset from response if available
                    current_offset = data.get("data", {}).get("next_offset", current_offset + 18)
                else:
                    break
            except Exception as e:
                logger.error(f"Error fetching home offset {current_offset}: {e}")
                break
    
    return all_dramas

# Compatibility alias for old sources
async def get_latest_idramas(pages=1):
    return await get_latest_dramas(pages=pages)

async def get_drama_detail(book_id: str):
    """Fetches drama detail from Melolo API."""
    url = f"{BASE_URL}/detail/{book_id}"
    params = {"lang": "id"}
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data and data.get("code") == 0:
                return data
            return None
        except Exception as e:
            logger.error(f"Error fetching drama detail for {book_id}: {e}")
            return None

# Compatibility alias
async def get_idrama_detail(book_id: str):
    return await get_drama_detail(book_id)

async def get_all_episodes(book_id: str):
    """Fetches episodes list from drama detail."""
    detail = await get_drama_detail(book_id)
    if detail and "videos" in detail:
        return detail["videos"]
    return []

# Compatibility alias
async def get_idrama_all_episodes(book_id: str):
    return await get_all_episodes(book_id)

async def search_dramas(query: str):
    """Searches dramas by title."""
    url = f"{BASE_URL}/search"
    params = {
        "lang": "id",
        "q": query
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data and data.get("code") == 0:
                # Search structure: data has 'data' which is list of books?
                # Looking at my test: {"code":0,"count":33,"data":[...]}
                return data.get("data", [])
            return []
        except Exception as e:
            logger.error(f"Error searching for {query}: {e}")
            return []

async def get_video_url(vid: str):
    """Fetches the actual play URL for a video ID."""
    url = f"{BASE_URL}/video/{vid}"
    params = {
        "lang": "id",
        "code": AUTH_CODE
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            # Response: {"backup": "...", "url": "..."}
            return data.get("url") or data.get("backup")
        except Exception as e:
            logger.error(f"Error fetching video URL for {vid}: {e}")
            return None
