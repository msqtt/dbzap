import asyncio
import sys

import uvicorn

from dbzap.core.config import get_settings
from dbzap.server.app import create_app


def serve() -> None:
    settings = get_settings()
    try:
        app = asyncio.run(create_app(settings=settings))
    except ConnectionError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    serve()
