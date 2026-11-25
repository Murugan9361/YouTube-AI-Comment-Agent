"""Encryption Service for Securing OAuth Access and Refresh Tokens at Rest.
Uses AES-128-CBC + HMAC-SHA256 (Fernet) key derivation from SECRET_KEY.
"""

import base64
import hashlib
import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()


class CryptoService:
    """Provides symmetric encryption and decryption for sensitive tokens stored at rest."""

    PREFIX = "enc:"

    def __init__(self, secret_key: Optional[str] = None) -> None:
        raw_key = (
            secret_key
            or os.getenv("SECRET_KEY")
            or os.getenv("GOOGLE_CLIENT_SECRET")
            or "default-secret-key-youtube-comment-agent-32b"
        )
        # Derive 32-byte key digest for Fernet
        key_digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
        self._fernet_key = base64.urlsafe_b64encode(key_digest)
        self._cipher = Fernet(self._fernet_key)

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        """
        Encrypts a raw string token and prepends 'enc:'.
        If input is empty or already encrypted, returns as-is.
        """
        if not plaintext or not isinstance(plaintext, str):
            return plaintext
        if plaintext.startswith(self.PREFIX):
            return plaintext
        encrypted_bytes = self._cipher.encrypt(plaintext.encode("utf-8"))
        return f"{self.PREFIX}{encrypted_bytes.decode('utf-8')}"

    def decrypt(self, ciphertext: Optional[str]) -> Optional[str]:
        """
        Decrypts an encrypted string token starting with 'enc:'.
        If input is empty or not prefixed with 'enc:' (legacy plain text token),
        returns as-is for backward compatibility.
        """
        if not ciphertext or not isinstance(ciphertext, str):
            return ciphertext
        if not ciphertext.startswith(self.PREFIX):
            # Backward compatibility for existing unencrypted tokens
            return ciphertext
        raw_cipher = ciphertext[len(self.PREFIX):]
        try:
            decrypted_bytes = self._cipher.decrypt(raw_cipher.encode("utf-8"))
            return decrypted_bytes.decode("utf-8")
        except (InvalidToken, Exception):
            # Fallback if decryption fails
            return ciphertext

    def is_encrypted(self, value: Optional[str]) -> bool:
        """Checks if a string is encrypted with the enc: prefix."""
        return bool(value and isinstance(value, str) and value.startswith(self.PREFIX))


crypto_service = CryptoService()
