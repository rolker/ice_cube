"""Status page and JSON API, reached through Home Assistant Ingress.

The page uses relative URLs only, because Ingress serves it under a per-session path.
"""
import functools
import json
from pathlib import Path

from aiohttp import web

STATIC = Path(__file__).parent / "static"
INGRESS_PROXY = "172.30.32.2"
NO_STORE = {"Cache-Control": "no-store"}
# Strict JSON: a NaN or infinity is an error here, never sent to the page.
STRICT_JSON = functools.partial(json.dumps, allow_nan=False)


def create_app(get_status, allowed_clients=(INGRESS_PROXY,), get_track=None):
    @web.middleware
    async def only_ingress(request, handler):
        if allowed_clients and request.remote not in allowed_clients:
            raise web.HTTPForbidden(text="Open this page through Home Assistant.\n")
        return await handler(request)

    async def index(_request):
        return web.FileResponse(STATIC / "index.html")

    async def track_script(_request):
        return web.FileResponse(STATIC / "track.js")

    async def status(_request):
        return web.json_response(get_status(), headers=NO_STORE, dumps=STRICT_JSON)

    async def track(request):
        """`since`, if given, is the `cursor` of an earlier response."""
        if get_track is None:
            raise web.HTTPNotFound()
        return web.json_response(get_track(request.query.get("since")), headers=NO_STORE,
                                 dumps=STRICT_JSON)

    app = web.Application(middlewares=[only_ingress])
    app.router.add_get("/", index)
    app.router.add_get("/track.js", track_script)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/track", track)
    app.router.add_static("/vendor/", STATIC / "vendor", name="vendor")
    return app
