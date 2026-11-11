"""Status page and JSON API, reached through Home Assistant Ingress.

The page uses relative URLs only, because Ingress serves it under a per-session path.

Requests that talk to the receiver are POSTs with a JSON body. Requiring the JSON
content type means another web site cannot send them from a plain form. Changes are
two-step: the first request returns a pending id with the exact command, and only
`api/command/confirm` with that id sends it. The block and confirm rules are applied
here on the server, in commands.prepare, whatever the page does.
"""
import functools
import json
from pathlib import Path

from aiohttp import web

from .control import RequestError

STATIC = Path(__file__).parent / "static"
INGRESS_PROXY = "172.30.32.2"
# On the API, and on the page and its scripts too: a browser that kept an old copy of
# the page after an update showed the old cards. The script tags also carry the
# version (`track.js?v=...`), so a cached page can never load a stale script.
NO_STORE = {"Cache-Control": "no-store"}
# Strict JSON: a NaN or infinity is an error here, never sent to the page.
STRICT_JSON = functools.partial(json.dumps, allow_nan=False)
# Set by Home Assistant Ingress. Recorded in the command log, never trusted: Ingress
# already restricts the page to logged-in users (administrators, by default).
USER_HEADERS = {"id": "X-Remote-User-Id", "name": "X-Remote-User-Name",
                "display_name": "X-Remote-User-Display-Name"}
MAX_BODY = 4096
TRACK_SOURCES = ("gga", "measured")
PLOT_WINDOWS = ("1h", "24h", "7d")


def ingress_user(request):
    return {key: request.headers[header][:200] for key, header in USER_HEADERS.items()
            if header in request.headers}


def create_app(get_status, allowed_clients=(INGRESS_PROXY,), get_track=None, control=None,
               get_plots=None):
    @web.middleware
    async def only_ingress(request, handler):
        if allowed_clients and request.remote not in allowed_clients:
            raise web.HTTPForbidden(text="Open this page through Home Assistant.\n")
        return await handler(request)

    def reply(data, status=200):
        return web.json_response(data, status=status, headers=NO_STORE, dumps=STRICT_JSON)

    async def index(_request):
        return web.FileResponse(STATIC / "index.html", headers=NO_STORE)

    async def track_script(_request):
        return web.FileResponse(STATIC / "track.js", headers=NO_STORE)

    async def page_script(_request):
        return web.FileResponse(STATIC / "control.js", headers=NO_STORE)

    async def status(_request):
        return reply(get_status())

    async def track(request):
        """`since`, if given, is the `cursor` of an earlier response; `source` (gga or
        measured), if given, forces that history instead of the app's choice."""
        if get_track is None:
            raise web.HTTPNotFound()
        source = request.query.get("source")
        if source is None:
            return reply(get_track(request.query.get("since")))
        if source not in TRACK_SOURCES:
            return reply({"error": "source must be gga or measured."}, 400)
        return reply(get_track(request.query.get("since"), source))

    async def plots(request):
        """The reference plots' data: `window` 1h, 24h (default) or 7d."""
        if get_plots is None:
            raise web.HTTPNotFound()
        window = request.query.get("window", "24h")
        if window not in PLOT_WINDOWS:
            return reply({"error": "window must be 1h, 24h or 7d."}, 400)
        return reply(get_plots(window))

    def receiver_route(handler):
        """A POST with a JSON object body; RequestError becomes a 400 with its text."""
        async def wrapped(request):
            if control is None:
                raise web.HTTPNotFound()
            if request.content_type != "application/json":
                return reply({"error": "Send JSON (Content-Type: application/json)."}, 415)
            if (request.content_length or 0) > MAX_BODY:
                return reply({"error": "Request too large."}, 413)
            try:
                body = json.loads(await request.text())
            except ValueError:
                return reply({"error": "The request body is not JSON."}, 400)
            if not isinstance(body, dict):
                return reply({"error": "The request body must be a JSON object."}, 400)
            try:
                return reply(await handler(body, ingress_user(request)))
            except RequestError as error:
                return reply({"error": str(error)}, 400)
        return wrapped

    async def command(body, user):
        text = body.get("command")
        if not isinstance(text, str):
            raise RequestError("Give the command as text.")
        return await control.command(text, user)

    async def confirm(body, user):
        return await control.confirm(body.get("id"), user)

    async def profile_check(_body, user):
        return {"profile": await control.check_profile(user)}

    async def profile_apply(_body, user):
        return await control.apply_profile(user)

    async def base_mode(_body, _user):
        return control.step_base_mode()

    async def survey_in(body, _user):
        return control.step_survey_in(body.get("count", 300))

    async def fixed(body, _user):
        return control.step_fixed(body)

    async def history(_request):
        if control is None:
            raise web.HTTPNotFound()
        return reply({"entries": control.history()})

    app = web.Application(middlewares=[only_ingress], client_max_size=MAX_BODY)
    app.router.add_get("/", index)
    app.router.add_get("/track.js", track_script)
    app.router.add_get("/control.js", page_script)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/track", track)
    app.router.add_get("/api/commands", history)
    app.router.add_get("/api/references/plots", plots)
    for path, handler in (("/api/command", command), ("/api/command/confirm", confirm),
                          ("/api/profile/check", profile_check),
                          ("/api/profile/apply", profile_apply),
                          ("/api/steps/base-mode", base_mode),
                          ("/api/steps/survey-in", survey_in),
                          ("/api/steps/fixed", fixed)):
        app.router.add_post(path, receiver_route(handler))
    app.router.add_static("/vendor/", STATIC / "vendor", name="vendor")
    return app
