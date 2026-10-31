"""Content-app routes for the provider network mirror protocol.

pulpcore's content app imports ``<plugin>.app.content`` at startup. Routes register in
order and aiohttp takes the first match, so ``index.json`` precedes ``{version}.json``,
and the checksum route precedes the archive route, which its paths would also match.
"""

from aiohttp import web
from django.conf import settings

from pulpcore.plugin.content import app

from .mirror import TofuMirror
from .models import MIRROR_PATH_PREFIX
from .registry import POOL

PREFIX = f"{MIRROR_PATH_PREFIX}{{pulp_domain}}/" if settings.DOMAIN_ENABLED else MIRROR_PATH_PREFIX
PROVIDER = PREFIX + "{base_path:.+}/{hostname}/{namespace}/{provider_type}/"

mirror = TofuMirror()

app.add_routes(
    [
        web.get(PROVIDER + "index.json", mirror.index),
        web.get(PROVIDER + "{version}.json", mirror.version),
        web.get(PROVIDER + "{version}/_checksums/{name}", mirror.checksums),
        web.get(PROVIDER + "{version}/{filename}", mirror.archive),
    ]
)


async def _close_registry_sessions(_app: web.Application) -> None:
    await POOL.close_all()


app.on_cleanup.append(_close_registry_sessions)
