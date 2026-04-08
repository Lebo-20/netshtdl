import asyncio
from api import search_dramas

async def find():
    res = await search_dramas("Hari Pembalasan")
    print(res)

asyncio.run(find())
