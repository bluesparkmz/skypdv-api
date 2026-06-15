import os
import httpx
import hmac
import hashlib
import json
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import HTTPException

class SkyWalletGatewayClient:
    def __init__(self):
        self.base_url = os.getenv("SKYWALLET_GATEWAY_URL", "https://skywallet-api.bluesparkmz.com")
        self.service_name = os.getenv("SKYWALLET_SERVICE_NAME", "skypdv")
        self.api_key = os.getenv("SKYWALLET_API_KEY", "")
        self.signing_secret = os.getenv("SKYWALLET_SIGNING_SECRET", "")
        
    def _generate_signature(self, timestamp: str, raw_body: bytes) -> str:
        message = f"{timestamp}.".encode("utf-8") + raw_body
        return hmac.new(
            self.signing_secret.encode("utf-8"),
            message,
            hashlib.sha256
        ).hexdigest()
    
    async def get_balance(self, central_user_id: str, user_details: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/bs/gateway/balance/{central_user_id}"
        timestamp = str(int(datetime.utcnow().timestamp()))
        raw_body = b""  # GET request has empty body
        signature = self._generate_signature(timestamp, raw_body)
        
        headers = {
            "X-Service-Name": self.service_name,
            "X-API-Key": self.api_key,
            "X-Signing-Secret": self.signing_secret,
            "X-Timestamp": timestamp,
            "X-Signature": signature
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers)
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Network error contacting SkyWallet gateway: {str(e)}")

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Failed to get balance from SkyWallet")
                except Exception:
                    error_detail = response.text or "Failed to get balance from SkyWallet"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            try:
                return response.json()
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"Invalid JSON response from SkyWallet gateway: {response.text[:200]}")
    
    async def charge(self, user_details: Dict[str, Any], amount: float, reference: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/bs/gateway/charge"
        timestamp = str(int(datetime.utcnow().timestamp()))
        payload_dict = {
            "user": user_details,
            "amount": amount,
            "reference": reference,
            "metadata": metadata or {}
        }
        # Serialize to JSON with consistent formatting (no extra spaces)
        raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        signature = self._generate_signature(timestamp, raw_body)
        
        headers = {
            "X-Service-Name": self.service_name,
            "X-API-Key": self.api_key,
            "X-Signing-Secret": self.signing_secret,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Idempotency-Key": f"{reference}-{datetime.utcnow().isoformat()}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, content=raw_body, headers=headers)
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Network error contacting SkyWallet gateway: {str(e)}")

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Failed to charge user from SkyWallet")
                except Exception:
                    error_detail = response.text or "Failed to charge user from SkyWallet"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            try:
                return response.json()
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"Invalid JSON response from SkyWallet gateway: {response.text[:200]}")
    
    async def deposit(self, user_details: Dict[str, Any], amount: float, msisdn: str, reference: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/bs/gateway/deposit"
        timestamp = str(int(datetime.utcnow().timestamp()))
        payload_dict = {
            "user": user_details,
            "amount": amount,
            "msisdn": msisdn,
            "reference": reference,
            "metadata": metadata or {}
        }
        raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        signature = self._generate_signature(timestamp, raw_body)
        
        headers = {
            "X-Service-Name": self.service_name,
            "X-API-Key": self.api_key,
            "X-Signing-Secret": self.signing_secret,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Idempotency-Key": f"deposit-{reference}-{datetime.utcnow().isoformat()}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, content=raw_body, headers=headers)
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Network error contacting SkyWallet gateway: {str(e)}")

            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Failed to initiate deposit from SkyWallet")
                except Exception:
                    error_detail = response.text or "Failed to initiate deposit from SkyWallet"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            try:
                return response.json()
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"Invalid JSON response from SkyWallet gateway: {response.text[:200]}")

    def sync_get_balance(self, central_user_id: str, user_details: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/bs/gateway/balance/{central_user_id}"
        timestamp = str(int(datetime.utcnow().timestamp()))
        raw_body = b""
        signature = self._generate_signature(timestamp, raw_body)
        
        headers = {
            "X-Service-Name": self.service_name,
            "X-API-Key": self.api_key,
            "X-Signing-Secret": self.signing_secret,
            "X-Timestamp": timestamp,
            "X-Signature": signature
        }
        
        with httpx.Client() as client:
            response = client.get(url, headers=headers)
            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Failed to get balance from SkyWallet")
                except Exception:
                    error_detail = response.text or "Failed to get balance from SkyWallet"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            try:
                return response.json()
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"Invalid JSON response from SkyWallet gateway: {response.text[:200]}")
    
    def sync_charge(self, user_details: Dict[str, Any], amount: float, reference: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/bs/gateway/charge"
        timestamp = str(int(datetime.utcnow().timestamp()))
        payload_dict = {
            "user": user_details,
            "amount": amount,
            "reference": reference,
            "metadata": metadata or {}
        }
        raw_body = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        signature = self._generate_signature(timestamp, raw_body)
        
        headers = {
            "X-Service-Name": self.service_name,
            "X-API-Key": self.api_key,
            "X-Signing-Secret": self.signing_secret,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Idempotency-Key": f"{reference}-{datetime.utcnow().isoformat()}",
            "Content-Type": "application/json"
        }
        
        with httpx.Client() as client:
            response = client.post(url, content=raw_body, headers=headers)
            if response.status_code != 200:
                try:
                    error_detail = response.json().get("detail", "Failed to charge user from SkyWallet")
                except Exception:
                    error_detail = response.text or "Failed to charge user from SkyWallet"
                raise HTTPException(status_code=response.status_code, detail=error_detail)
            try:
                return response.json()
            except json.JSONDecodeError:
                raise HTTPException(status_code=502, detail=f"Invalid JSON response from SkyWallet gateway: {response.text[:200]}")

