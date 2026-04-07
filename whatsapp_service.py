import base64
import os
from typing import Optional

import requests

BASE_WHATSAPP = os.getenv("WHATSAPP_BASE_URL", "https://bluesparkmz-api-sap.up.railway.app")
INSTANCE = os.getenv("WHATSAPP_INSTANCE", "Skyvenda MZ")

WHATSAPP_URL = os.getenv("WHATSAPP_URL", f"{BASE_WHATSAPP}/message/sendMedia/{INSTANCE}")
WHATSAPP_TEXT_URL = os.getenv("WHATSAPP_TEXT_URL", f"{BASE_WHATSAPP}/message/sendText/{INSTANCE}")
WHATSAPP_FILE_URL = os.getenv("WHATSAPP_FILE_URL", f"{BASE_WHATSAPP}/message/sendFile/{INSTANCE}")
API_KEY = os.getenv("API_KEY_WHATSAPP")


def _normalize_binary_content(content: bytes) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    if isinstance(content, memoryview):
        return content.tobytes()
    return bytes(content)


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


def send_whatsapp_file(
    number: str,
    filename: str,
    mime: str,
    content: bytes,
    caption: str = "",
) -> Optional[requests.Response]:
    """
    Envia primeiro o arquivo binario real via multipart/form-data.
    Se a instancia nao aceitar multipart, tenta fallback em JSON/base64.
    """
    if not API_KEY or not number:
        return None

    binary_content = _normalize_binary_content(content)

    files = {"file": (filename, binary_content, mime)}
    data = {"number": number, "caption": caption or "", "delay": 0}
    headers_mp = {"apikey": API_KEY}

    try:
        response = requests.post(
            WHATSAPP_FILE_URL,
            data=data,
            files=files,
            headers=headers_mp,
            timeout=30,
        )
        if response.status_code < 300:
            return response
        print("WhatsApp sendFile failed", response.status_code, response.text)
    except Exception as exc:
        print("WhatsApp sendFile exception", exc)

    media_b64 = base64.b64encode(binary_content).decode("utf-8")
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
        response = requests.post(WHATSAPP_URL, json=payload, headers=headers, timeout=30)
        if response.status_code >= 400:
            print("WhatsApp sendMedia failed", response.status_code, response.text)
        return response
    except Exception as exc:
        print("WhatsApp sendMedia exception", exc)
        return None
