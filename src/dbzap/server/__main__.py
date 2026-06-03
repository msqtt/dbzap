import asyncio
import sys

import uvicorn

from dbzap.core.config import get_settings
from dbzap.server.app import create_app


def serve() -> None:
    settings = get_settings()

    async def main() -> None:
        try:
            app = await create_app(settings=settings)
        except ConnectionError as exc:
            print(f"Startup failed: {exc}", file=sys.stderr)
            sys.exit(1)
        config = uvicorn.Config(app, host=settings.host, port=settings.port)
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(main())


if __name__ == "__main__":
    serve()
