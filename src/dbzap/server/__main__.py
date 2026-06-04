import asyncio
import sys

import uvicorn

from dbzap.server.app import create_app


def _load_settings():
    try:
        from dbzap.core.config import get_settings
        return get_settings()
    except Exception as exc:
        from pydantic import ValidationError
        if isinstance(exc, ValidationError):
            fields = ", ".join(e["loc"][0] for e in exc.errors() if e.get("loc"))
            print(
                f"❌ Missing required config: {fields}\n"
                f"   Set them as environment variables or in a .env file.",
                file=sys.stderr,
            )
        else:
            print(f"❌ Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)


def serve() -> None:
    settings = _load_settings()

    async def main() -> None:
        try:
            app = await create_app(settings=settings)
        except ConnectionError as exc:
            print(f"❌ Startup failed: {exc}", file=sys.stderr)
            sys.exit(1)
        config = uvicorn.Config(app, host=settings.host, port=settings.port)
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(main())


def version() -> None:
    import importlib.metadata
    try:
        v = importlib.metadata.version("dbzap")
    except importlib.metadata.PackageNotFoundError:
        v = "0.0.0-dev"
    print(f"dbzap {v}")


def inspect_schema() -> None:
    settings = _load_settings()

    async def main() -> None:
        from dbzap.core.engine import build_engine
        from dbzap.core.introspector import SchemaIntrospector
        engine = build_engine(settings)
        introspector = SchemaIntrospector(engine=engine)
        try:
            tables = await introspector.introspect()
        except ConnectionError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            sys.exit(1)
        for t in tables:
            pk = ", ".join(t.primary_key) or "(none)"
            print(f"\n{t.name} [PK: {pk}]")
            for col in t.columns:
                null = "NULL" if col.nullable else "NOT NULL"
                print(f"  {col.name:<20} {col.sql_type:<15} {null}")
        await engine.dispose()

    asyncio.run(main())


def healthcheck() -> None:
    import urllib.request
    url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000/healthz"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status == 200:
                print(f"✅ {url} — healthy")
            else:
                print(f"❌ {url} — status {resp.status}", file=sys.stderr)
                sys.exit(1)
    except Exception as exc:
        print(f"❌ {url} — {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    commands = {
        "serve": serve,
        "version": version,
        "inspect": inspect_schema,
        "healthcheck": healthcheck,
    }
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd in ("-h", "--help"):
        print("Usage: dbzap <command>\n\nCommands: serve, version, inspect, healthcheck [url]")
        return
    if cmd not in commands:
        print(f"Unknown command: {cmd}\nAvailable: {', '.join(commands)}", file=sys.stderr)
        sys.exit(1)
    commands[cmd]()


if __name__ == "__main__":
    main()
