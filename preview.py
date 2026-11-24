"""Read-only UI preview. Personal data and payments require the Telegram bot server."""
from pathlib import Path
from aiohttp import web

root = Path(__file__).parent / "web"
app = web.Application()


async def index(request):
    return web.FileResponse(root / "index.html")


app.router.add_get("/", index)
app.router.add_static("/assets/", root)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8090)
