"""Owned Uvicorn process; a stdin command or parent EOF requests safe shutdown."""
import argparse
import asyncio
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn
from backend.main import app


async def serve(port, instance):
    @app.get('/_desktop/health', include_in_schema=False)
    async def desktop_health():
        return {'instance': instance}

    server = uvicorn.Server(uvicorn.Config(
        app, host='127.0.0.1', port=port, timeout_graceful_shutdown=3,
    ))
    loop = asyncio.get_running_loop()

    def watch_parent():
        # EOF also handles the launcher unexpectedly exiting, without orphaning us.
        while True:
            message = sys.stdin.readline()
            if not message or message.strip() == 'stop':
                if not loop.is_closed():
                    loop.call_soon_threadsafe(setattr, server, 'should_exit', True)
                return

    threading.Thread(target=watch_parent, daemon=True).start()
    await server.serve()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--instance', required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.instance))
