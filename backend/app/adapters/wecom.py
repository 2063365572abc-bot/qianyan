"""Official self-built app text messages and authenticated, bound-user callbacks.

Protocol: SHA1(sorted(token,timestamp,nonce,ciphertext)); AES-256-CBC with
the first 16 key bytes as IV; PKCS7 block size 32; random16 + uint32BE length
+ message + CorpID. Matches the official @wecom/crypto protocol.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import struct
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .types import AdapterError, HTTPAdapter, fingerprint


class WeComAdapter(HTTPAdapter):
    def __init__(
        self, corp_id: str, agent_id: int | str, secret: str, user_id: str,
        callback_token: str = "", encoding_aes_key: str = "", *,
        client: httpx.AsyncClient | None = None, transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://qyapi.weixin.qq.com", timeout: float = 15.0,
        callback_max_skew: int | None = 300,
    ):
        if not corp_id or not secret or not user_id or user_id == "@all" or "|" in user_id:
            raise AdapterError("configuration", "WeCom requires an application and one bound recipient.")
        try:
            numeric_agent = int(agent_id)
            if numeric_agent <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise AdapterError("configuration", "WeCom requires a valid application AgentID.") from None
        super().__init__(base_url, client=client, transport=transport, timeout=timeout)
        self.corp_id, self.agent_id, self.user_id = corp_id, numeric_agent, user_id
        self._secret, self._callback_token = secret, callback_token
        self._encoding_aes_key = encoding_aes_key
        self.callback_max_skew = callback_max_skew
        self._access_token = ""
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _get_token(self) -> str:
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            data, _ = await self.request_json(
                "GET", "/cgi-bin/gettoken", params={"corpid": self.corp_id, "corpsecret": self._secret},
            )
            if not isinstance(data, dict) or data.get("errcode", 0) != 0 or not data.get("access_token"):
                raise AdapterError("wecom_token", "WeCom rejected application authentication.")
            try:
                expires = min(int(data.get("expires_in", 7200)), 86400)
                if expires <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise AdapterError("invalid_response", "WeCom returned an invalid token expiry.") from None
            self._access_token = data["access_token"]
            self._expires_at = time.monotonic() + max(1, expires - 60)
            return self._access_token

    async def send_text(self, text: str) -> dict:
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 2048:
            raise AdapterError("input", "WeCom text must contain at most 2048 UTF-8 bytes.")
        for attempt in range(2):
            token = await self._get_token()
            try:
                data, _ = await self.request_json(
                    "POST", "/cgi-bin/message/send", params={"access_token": token},
                    payload={"touser": self.user_id, "msgtype": "text", "agentid": self.agent_id,
                             "text": {"content": text}, "enable_duplicate_check": 1, "duplicate_check_interval": 1800},
                )
            except AdapterError as exc:
                if exc.code in ("timeout", "network"):
                    # The provider may have accepted the request before disconnect.
                    raise AdapterError("delivery_unknown", "WeCom delivery is uncertain; inspect before retrying.") from None
                raise
            if not isinstance(data, dict):
                raise AdapterError("invalid_response", "WeCom returned an invalid send response.")
            code = data.get("errcode")
            if code in (40014, 42001) and attempt == 0:
                self._access_token, self._expires_at = "", 0
                continue
            if code != 0 or data.get("invaliduser") or data.get("invalidparty") or data.get("invalidtag"):
                raise AdapterError("wecom_send", "WeCom rejected the message or bound recipient.", code in (45009, -1))
            return {"accepted": True, "provider_id": str(data["msgid"]) if data.get("msgid") is not None else None}
        raise AdapterError("wecom_send", "WeCom rejected the message.")

    def _key(self) -> bytes:
        if not self._callback_token or len(self._encoding_aes_key) != 43:
            raise AdapterError("configuration", "WeCom callback Token and 43-character EncodingAESKey are required.")
        try:
            key = base64.b64decode(self._encoding_aes_key + "=", validate=True)
            if len(key) != 32:
                raise ValueError
            return key
        except (ValueError, binascii.Error):
            raise AdapterError("configuration", "WeCom callback EncodingAESKey is invalid.") from None

    def _decrypt(self, query: Mapping[str, str], encrypted: str) -> bytes:
        key = self._key()
        try:
            signature, timestamp, nonce = (query[name] for name in ("msg_signature", "timestamp", "nonce"))
            if any(not isinstance(value, str) or len(value) > 200 for value in (signature, timestamp, nonce)):
                raise ValueError
            expected = hashlib.sha1("".join(sorted((self._callback_token, timestamp, nonce, encrypted))).encode()).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError
            ciphertext = base64.b64decode(encrypted, validate=True)
            if not ciphertext or len(ciphertext) > 131_072 or len(ciphertext) % 16:
                raise ValueError
            decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            pad = padded[-1]
            if not 1 <= pad <= 32 or padded[-pad:] != bytes([pad]) * pad:
                raise ValueError
            plaintext = padded[:-pad]
            if len(plaintext) < 20:
                raise ValueError
            length = struct.unpack(">I", plaintext[16:20])[0]
            if 20 + length > len(plaintext):
                raise ValueError
            recipient = plaintext[20 + length:].decode("utf-8")
            if not hmac.compare_digest(recipient.encode(), self.corp_id.encode()):
                raise ValueError
            return plaintext[20:20 + length]
        except (KeyError, ValueError, UnicodeError, TypeError, struct.error):
            raise AdapterError("callback_invalid", "WeCom callback failed signature, cipher or recipient validation.") from None

    def verify_url(self, query: Mapping[str, str]) -> str:
        encrypted = query.get("echostr", "")
        if not isinstance(encrypted, str) or len(encrypted) > 180_000:
            raise AdapterError("callback_invalid", "Invalid WeCom URL verification request.")
        try:
            return self._decrypt(query, encrypted).decode("utf-8")
        except UnicodeError:
            raise AdapterError("callback_invalid", "Invalid WeCom verification plaintext.") from None

    @staticmethod
    def _xml(value: str | bytes) -> ET.Element:
        raw = value.encode() if isinstance(value, str) else value
        if not isinstance(raw, bytes) or len(raw) > 180_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise AdapterError("callback_invalid", "Invalid WeCom callback XML.")
        try:
            element = ET.fromstring(raw)
            if element.tag != "xml":
                raise ET.ParseError
            return element
        except ET.ParseError:
            raise AdapterError("callback_invalid", "Invalid WeCom callback XML.") from None

    def parse_callback(self, query: Mapping[str, str], xml: str | bytes) -> dict:
        outer = self._xml(xml)
        encrypted = outer.findtext("Encrypt")
        if not encrypted:
            raise AdapterError("callback_invalid", "WeCom callback requires encrypted content.")
        plaintext = self._decrypt(query, encrypted)
        if self.callback_max_skew is not None:
            try:
                if abs(time.time() - int(query["timestamp"])) > self.callback_max_skew:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise AdapterError("callback_expired", "WeCom callback timestamp is outside the accepted window.") from None
        inner = self._xml(plaintext)
        corp_id, sender = inner.findtext("ToUserName"), inner.findtext("FromUserName")
        if corp_id != self.corp_id or sender != self.user_id:
            raise AdapterError("callback_recipient", "WeCom callback is not from the bound user and enterprise.")
        if inner.findtext("AgentID") != str(self.agent_id):
            raise AdapterError("callback_recipient", "WeCom callback belongs to another application.")
        msg_type = inner.findtext("MsgType")
        text = inner.findtext("Content", "") if msg_type == "text" else ""
        if len(text.encode()) > 8192:
            raise AdapterError("callback_invalid", "WeCom callback text exceeds the read limit.")
        event = inner.findtext("Event")
        event_key = inner.findtext("EventKey")
        created = inner.findtext("CreateTime")
        message_id = inner.findtext("MsgId")
        if not message_id:
            message_id = fingerprint({"sender": sender, "created": created, "type": msg_type, "event": event, "event_key": event_key})
        return {"message_id": message_id, "user_id": sender, "corp_id": corp_id,
                "agent_id": self.agent_id, "message_type": msg_type, "text": text,
                "created_at": created, "event": event, "event_key": event_key}
