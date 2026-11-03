"""WebSocket endpoint broadcasting real-time task progress across Desktop and iPhone clients."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()


@ws_router.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket) -> None:
    await websocket.accept()
    app = websocket.app
    db = app.state.db
    mgr = app.state.queue_mgr
    loop = asyncio.get_running_loop()

    sub_queue = mgr.subscribe(loop)
    try:
        # Push initial snapshot upon connect
        await websocket.send_json({"type": "snapshot", "data": db.list_tasks(limit=200)})

        while True:
            # Wait for queue messages while periodically sending keepalive pings
            try:
                event = await asyncio.wait_for(sub_queue.get(), timeout=20.0)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        mgr.unsubscribe(sub_queue)
