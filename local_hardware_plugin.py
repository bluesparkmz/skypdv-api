from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from controllers.hardware_plugin_manager import HardwarePluginManager


plugin_manager = HardwarePluginManager()

app = FastAPI(
    title="SkyPDV Local Hardware Plugin",
    version="0.1.0",
    description="Plugin local em FastAPI/WebSocket para impressão térmica e gaveta de dinheiro.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "skypdv-local-hardware-plugin",
        "timestamp": datetime.utcnow().isoformat(),
        "selected_printer": plugin_manager.selected_printer,
    }


def _response(message_type: str, request_id: str | None, **kwargs: Any) -> dict[str, Any]:
    data = {"type": message_type}
    if request_id:
        data["request_id"] = request_id
    data.update(kwargs)
    return data


async def _handle_message(payload: dict[str, Any]) -> dict[str, Any]:
    message_type = payload.get("type")
    request_id = payload.get("request_id")

    if message_type == "ping":
        return _response("pong", request_id, success=True, message="pong")

    if message_type == "list_printers":
        result = plugin_manager.list_printers()
        return _response("list_printers", request_id, **result.to_dict())

    if message_type == "set_printer":
        result = plugin_manager.set_printer(str(payload.get("printer_name") or "").strip())
        return _response("set_printer", request_id, **result.to_dict())

    if message_type == "print":
        result = plugin_manager.print_receipt(
            str(payload.get("content") or ""),
            printer_name=str(payload.get("printer_name") or "").strip() or None,
        )
        return _response("print", request_id, **result.to_dict())

    if message_type == "list_ports":
        result = plugin_manager.list_serial_ports()
        return _response("list_ports", request_id, **result.to_dict())

    if message_type == "open_drawer":
        result = plugin_manager.open_cash_drawer(
            port=str(payload.get("port") or "").strip() or None
        )
        return _response("open_drawer", request_id, **result.to_dict())

    return _response(
        message_type or "unknown",
        request_id,
        success=False,
        error=f"Unsupported message type: {message_type}",
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "connected",
            "success": True,
            "message": "SkyPDV local hardware plugin connected",
        }
    )

    try:
        while True:
            payload = await websocket.receive_json()
            response = await _handle_message(payload)
            await websocket.send_json(response)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json(
            {
                "type": "error",
                "success": False,
                "error": str(exc),
            }
        )
        await websocket.close(code=1011)

