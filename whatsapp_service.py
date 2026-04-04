import os
import requests
import base64
from typing import Optional

WHATSAPP_URL = os.getenv(
    "WHATSAPP_URL",
    "https://bluesparkmz-api-sap.up.railway.app/message/sendMedia/Skyvenda MZ",
)
WHATSAPP_TEXT_URL = os.getenv(
    "WHATSAPP_TEXT_URL",
    "https://bluesparkmz-api-sap.up.railway.app/message/sendText/Skyvenda MZ",
)
API_KEY = os.getenv("API_KEY_WHATSAPP")


def send_whatsapp_text(number: str, text: str) -> Optional[requests.Response]:
    if not API_KEY or not number:
        return None
    payload = {
        "number": number,
        "text": text,
        "delay": 0,
        "linkPreview": False,
    }
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    try:
        return requests.post(WHATSAPP_TEXT_URL, json=payload, headers=headers, timeout=15)
    except Exception:
        return None


def send_whatsapp_file(number: str, filename: str, mime: str, content: bytes, caption: str = "") -> Optional[requests.Response]:
    """
    Envia documento via Evolution API (sendMedia) usando payload JSON com base64.
    mediaType: document (pdf/xlsx).
    """
    if not API_KEY or not number:
        return None

    media_b64 = base64.b64encode(content).decode("utf-8")
    payload = {
        "number": number,
        "mediaMessage": {
            "mediaType": "document",
            "fileName": filename,
            "caption": caption or "",
            "media": media_b64,
            "mimeType": mime,
        },
        "options": {"delay": 0, "presence": "composing"},
    }
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    try:
        return requests.post(WHATSAPP_URL, json=payload, headers=headers, timeout=30)
    except Exception:
        return None
