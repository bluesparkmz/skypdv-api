import os
import requests
from typing import Optional

WHATSAPP_URL = os.getenv(
    "WHATSAPP_URL",
    "https://bluesparkmz-api-sap.up.railway.app/message/sendFile/Skyvenda MZ",
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


def send_whatsapp_file(number: str, filename: str, mime: str, content: bytes) -> Optional[requests.Response]:
    """
    Envia um documento (PDF/XLSX) para o número informado.
    """
    if not API_KEY or not number:
        return None

    files = {
        "file": (filename, content, mime),
    }
    data = {
        "number": number,
        "delay": 0,
    }
    headers = {"apikey": API_KEY}
    try:
        return requests.post(WHATSAPP_URL, data=data, files=files, headers=headers, timeout=20)
    except Exception:
        return None
