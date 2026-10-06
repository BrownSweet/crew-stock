"""Expose the app at the same prefix baked into the frontend."""

from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route


def mount_public_path(app, base_path):
    if base_path == "/":
        return app

    async def redirect(request):
        query = request.url.query
        return RedirectResponse(
            base_path + ("?" + query if query else ""), status_code=308
        )

    return Starlette(
        lifespan=app.router.lifespan_context,
        routes=[
            Route("/", redirect),
            Route(base_path.rstrip("/"), redirect),
            Mount(base_path.rstrip("/"), app=app),
        ],
    )
