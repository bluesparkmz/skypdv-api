import os
import requests
import base64
from typing import Optional
from urllib.parse import quote_plus

BASE_WHATSAPP = os.getenv("WHATSAPP_BASE_URL", "https://bluesparkmz-api-sap.up.railway.app")
INSTANCE = quote_plus(os.getenv("WHATSAPP_INSTANCE", "Skyvenda MZ"))

WHATSAPP_URL = os.getenv("WHATSAPP_URL", f"{BASE_WHATSAPP}/message/sendMedia/{INSTANCE}")
WHATSAPP_TEXT_URL = os.getenv("WHATSAPP_TEXT_URL", f"{BASE_WHATSAPP}/message/sendText/{INSTANCE}")
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
        resp = requests.post(WHATSAPP_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code >= 400:
            print("WhatsApp sendMedia failed", resp.status_code, resp.text)
        return resp
    except Exception as e:
        print("WhatsApp sendMedia exception", e)
        return None
