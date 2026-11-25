"""Google OAuth 2.0, Multi-Account Store, and Session Management Service."""

import base64
import hashlib
import os
import secrets
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.services.database import db


class OAuthService:
    """Manages Google OAuth 2.0 flow, PKCE, token refresh, and multi-account SQLite storage."""

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
    GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
    YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._oauth_states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _get_config(self) -> Dict[str, str]:
        """Loads and returns OAuth configuration from environment variables."""
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback").strip()
        base_scopes = os.getenv("GOOGLE_SCOPES", "https://www.googleapis.com/auth/youtube.force-ssl").strip()

        # Ensure openid and email are requested to identify the Google account
        scope_list = base_scopes.split()
        if "openid" not in scope_list:
            scope_list.append("openid")
        if "https://www.googleapis.com/auth/userinfo.email" not in scope_list and "email" not in scope_list:
            scope_list.append("https://www.googleapis.com/auth/userinfo.email")
        if "https://www.googleapis.com/auth/userinfo.profile" not in scope_list and "profile" not in scope_list:
            scope_list.append("https://www.googleapis.com/auth/userinfo.profile")

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scopes": " ".join(scope_list),
        }

    def _generate_pkce(self) -> Tuple[str, str]:
        """Generates a PKCE code_verifier and S256 code_challenge."""
        code_verifier = secrets.token_urlsafe(64)
        hashed = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(hashed).decode("utf-8").replace("=", "")
        return code_verifier, code_challenge

    def get_authorization_url(
        self,
        session_id: str,
        target_account_id: Optional[str] = None,
        prompt: str = "select_account consent",
    ) -> str:
        """
        Generates the Google OAuth consent URL with state validation and PKCE.
        Passes target_account_id in state so reauthorization binds directly to the selected account.
        Forces prompt='select_account consent' so Google always prompts the account chooser.
        """
        config = self._get_config()
        if not config["client_id"] or not config["client_secret"]:
            raise ValueError(
                "Google OAuth credentials are missing. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env."
            )

        code_verifier, code_challenge = self._generate_pkce()
        state = secrets.token_urlsafe(32)

        with self._lock:
            # Clean expired states older than 15 minutes
            now = time.time()
            expired_keys = [k for k, v in self._oauth_states.items() if now - v["created_at"] > 900]
            for k in expired_keys:
                self._oauth_states.pop(k, None)

            self._oauth_states[state] = {
                "session_id": session_id,
                "target_account_id": target_account_id,
                "code_verifier": code_verifier,
                "created_at": now,
            }

        params = {
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "response_type": "code",
            "scope": config["scopes"],
            "access_type": "offline",
            "prompt": prompt,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        query_string = urllib.parse.urlencode(params)
        return f"{self.GOOGLE_AUTH_URL}?{query_string}"

    async def fetch_google_user_info(self, client: httpx.AsyncClient, access_token: str) -> Dict[str, Any]:
        """Fetches the authenticated Google account email, name, and profile photo."""
        try:
            resp = await client.get(
                self.GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "email": data.get("email", ""),
                    "name": data.get("name", ""),
                    "picture": data.get("picture", ""),
                }
        except Exception:
            pass
        return {"email": "", "name": "", "picture": ""}

    async def fetch_channel_info(self, client: httpx.AsyncClient, access_token: str) -> Dict[str, Any]:
        """
        Fetches channel metadata for the authenticated user using channels.list with mine=true.
        Accurately differentiates between a Google account and an active YouTube channel.
        Never returns placeholder or fake data.
        """
        try:
            resp = await client.get(
                self.YOUTUBE_CHANNELS_URL,
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    snippet = items[0].get("snippet", {})
                    thumbnails = snippet.get("thumbnails", {})
                    thumb_url = (
                        thumbnails.get("default", {}).get("url")
                        or thumbnails.get("medium", {}).get("url")
                        or ""
                    )
                    return {
                        "has_channel": True,
                        "id": items[0].get("id", ""),
                        "title": snippet.get("title", ""),
                        "thumbnail": thumb_url,
                        "custom_url": snippet.get("customUrl", ""),
                    }
                else:
                    return {
                        "has_channel": False,
                        "id": None,
                        "title": None,
                        "thumbnail": None,
                        "message": (
                            "No YouTube channel associated with this Google account. "
                            "Please create a YouTube channel at https://www.youtube.com/create_channel."
                        ),
                    }
            elif resp.status_code == 403:
                return {
                    "has_channel": False,
                    "id": None,
                    "title": None,
                    "thumbnail": None,
                    "message": "YouTube access forbidden (ineligibleAccount). Ensure a YouTube channel is created at https://www.youtube.com/create_channel.",
                }
            else:
                return {
                    "has_channel": False,
                    "id": None,
                    "title": None,
                    "thumbnail": None,
                    "message": f"YouTube API returned error status {resp.status_code}: {resp.text}",
                }
        except Exception as e:
            return {
                "has_channel": False,
                "id": None,
                "title": None,
                "thumbnail": None,
                "message": f"Failed to query YouTube channels: {str(e)}",
            }

    async def exchange_code(self, code: str, state: str) -> Dict[str, Any]:
        """
        Validates state, exchanges authorization code for tokens, verifies YouTube channel,
        and saves account to multi-account persistent database without fake IDs or overwrites.
        """
        with self._lock:
            state_data = self._oauth_states.pop(state, None)

        if not state_data:
            raise ValueError("Invalid or expired OAuth state parameter. Please try connecting again.")

        session_id = state_data["session_id"]
        target_account_id = state_data.get("target_account_id")
        code_verifier = state_data["code_verifier"]

        config = self._get_config()
        payload = {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": config["redirect_uri"],
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.GOOGLE_TOKEN_URL, data=payload)
            if resp.status_code != 200:
                err_detail = resp.text
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("error_description") or err_json.get("error") or err_detail
                except Exception:
                    pass
                raise ValueError(f"Google token exchange failed ({resp.status_code}): {err_detail}")

            token_data = resp.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)

            if not access_token:
                raise ValueError("No access token returned from Google OAuth.")

            # 1. Fetch Google User Profile (email, name, picture)
            google_user = await self.fetch_google_user_info(client, access_token)
            google_email = google_user.get("email", "")

            # 2. Fetch authenticated YouTube channel details
            channel_info = await self.fetch_channel_info(client, access_token)

        has_channel = channel_info.get("has_channel", False)
        channel_id = channel_info.get("id")
        channel_title = channel_info.get("title")
        channel_thumbnail = channel_info.get("thumbnail") or google_user.get("picture") or ""

        # Cleanup any legacy placeholder records from previous runs
        db.cleanup_placeholder_accounts()

        # CASE 1: No active YouTube channel associated with this Google Account
        if not has_channel or not channel_id:
            # DO NOT generate fake channel IDs or placeholder names!
            # If target_account_id was a placeholder/invalid account, delete it:
            if target_account_id:
                target_acc = db.get_account(target_account_id)
                if target_acc and (target_acc.get("channel_id", "").startswith("channel_") or not target_acc.get("channel_id")):
                    db.delete_account(target_account_id)

            explanation = (
                f"The authenticated Google account '{google_email or 'selected account'}' has no associated YouTube channel. "
                "YouTube requires an active channel to post comments. "
                "Please visit https://www.youtube.com/create_channel while signed into that account to create your channel handle and profile, then try again."
            )
            return {
                "session_id": session_id,
                "account_id": None,
                "has_channel": False,
                "channel_title": None,
                "channel_id": None,
                "google_email": google_email,
                "message": explanation,
            }

        # CASE 2: YouTube Channel Found Successfully
        new_account_id = f"acc_{channel_id}"

        # Preserve existing refresh token if Google didn't return a new one on re-auth
        existing_acc = db.get_account(new_account_id)
        saved_refresh_token = refresh_token or (existing_acc.get("refresh_token") if existing_acc else "") or ""

        # Overwrite protection check:
        # If user intended to reauthorize target_account_id, but authorized a channel that is already connected elsewhere:
        if target_account_id and target_account_id != new_account_id:
            existing_other = db.get_account(new_account_id)
            if existing_other:
                # Update tokens for that account without overwriting or duplicate creation
                db.update_account_tokens(existing_other["account_id"], access_token, time.time() + int(expires_in), saved_refresh_token)
                # Clean up target account if it was a placeholder
                if target_account_id.startswith("acc_47") or "channel_" in target_account_id:
                    db.delete_account(target_account_id)
                return {
                    "session_id": session_id,
                    "account_id": existing_other["account_id"],
                    "has_channel": True,
                    "channel_title": channel_title,
                    "channel_id": channel_id,
                    "google_email": google_email,
                    "message": f"Channel '{channel_title}' tokens updated.",
                }
            else:
                # Target account was replaced by new valid channel
                db.delete_account(target_account_id)

        # Save to SQLite database (persists across restarts)
        db.save_account(
            account_id=new_account_id,
            channel_id=channel_id,
            channel_title=channel_title,
            channel_thumbnail=channel_thumbnail,
            access_token=access_token,
            refresh_token=saved_refresh_token,
            expires_at=time.time() + int(expires_in),
            status="active",
            google_email=google_email,
        )

        with self._lock:
            # Also store in memory session for backward compatibility
            self._sessions[session_id] = {
                "account_id": new_account_id,
                "access_token": access_token,
                "refresh_token": saved_refresh_token,
                "expires_at": time.time() + int(expires_in),
                "has_channel": True,
                "channel_title": channel_title,
                "channel_id": channel_id,
                "channel_thumbnail": channel_thumbnail,
                "google_email": google_email,
                "updated_at": time.time(),
            }

        return {
            "session_id": session_id,
            "account_id": new_account_id,
            "has_channel": True,
            "channel_title": channel_title,
            "channel_id": channel_id,
            "google_email": google_email,
            "message": f"YouTube channel '{channel_title}' connected successfully!",
        }

    # -------------------------------------------------------------------------
    # MULTI-ACCOUNT MANAGEMENT METHODS
    # -------------------------------------------------------------------------

    def list_accounts(self) -> List[Dict[str, Any]]:
        """
        Returns public metadata for all connected YouTube accounts.
        CRITICAL: Access and refresh tokens are strictly stripped to prevent token exposure.
        """
        accounts = db.list_accounts()
        return [
            {
                "account_id": acc["account_id"],
                "channel_id": acc["channel_id"],
                "channel_title": acc["channel_title"],
                "channel_thumbnail": acc.get("channel_thumbnail") or "",
                "status": acc.get("status", "active"),
                "is_active": bool(acc.get("is_active", 0)),
                "google_email": acc.get("google_email") or "",
                "error_message": acc.get("error_message") or "",
                "expires_at": acc.get("expires_at", 0),
                "created_at": acc.get("created_at"),
                "last_auth_at": acc.get("last_auth_at"),
            }
            for acc in accounts
        ]

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Returns public metadata for a specific connected account."""
        acc = db.get_account(account_id)
        if not acc:
            return None
        return {
            "account_id": acc["account_id"],
            "channel_id": acc["channel_id"],
            "channel_title": acc["channel_title"],
            "channel_thumbnail": acc.get("channel_thumbnail") or "",
            "status": acc.get("status", "active"),
            "is_active": bool(acc.get("is_active", 0)),
            "google_email": acc.get("google_email") or "",
            "error_message": acc.get("error_message") or "",
            "expires_at": acc.get("expires_at", 0),
            "created_at": acc.get("created_at"),
            "last_auth_at": acc.get("last_auth_at"),
        }

    def set_active_account(self, account_id: str) -> bool:
        """Sets the selected account as the default active account."""
        return db.set_active_account(account_id)

    def get_active_account(self) -> Optional[Dict[str, Any]]:
        """Returns public metadata for the currently active account."""
        acc = db.get_active_account()
        if not acc:
            return None
        return {
            "account_id": acc["account_id"],
            "channel_id": acc["channel_id"],
            "channel_title": acc["channel_title"],
            "channel_thumbnail": acc.get("channel_thumbnail") or "",
            "status": acc.get("status", "active"),
            "is_active": bool(acc.get("is_active", 0)),
            "google_email": acc.get("google_email") or "",
            "error_message": acc.get("error_message") or "",
            "expires_at": acc.get("expires_at", 0),
            "created_at": acc.get("created_at"),
            "last_auth_at": acc.get("last_auth_at"),
        }

    async def force_refresh_account_token(self, account_id: str) -> str:
        """
        Forces an immediate token refresh with Google for the specified account ID using its refresh_token.
        Used on demand and automatically when a 401 Unauthorized error occurs during API calls.
        Updates stored tokens (encrypted at rest) and resets status to 'active'.
        If Google rejects the refresh (revoked/invalid), marks status as 'reauth_required' and sets error_message.
        """
        acc = db.get_account(account_id)
        if not acc:
            raise PermissionError(f"YouTube account '{account_id}' is not connected. Please connect your account.")

        refresh_token = acc.get("refresh_token")
        if not refresh_token:
            err_msg = f"OAuth session for '{acc.get('channel_title', account_id)}' expired and no refresh token exists. Please reconnect account."
            db.update_account_status(account_id, "reauth_required", error_message=err_msg)
            raise PermissionError(err_msg)

        config = self._get_config()
        payload = {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.GOOGLE_TOKEN_URL, data=payload)
            if resp.status_code != 200:
                err_text = ""
                try:
                    err_json = resp.json()
                    err_text = err_json.get("error_description") or err_json.get("error") or resp.text
                except Exception:
                    err_text = resp.text

                err_msg = f"Google token refresh failed ({err_text}). Please click 'Reconnect Account'."
                db.update_account_status(account_id, "reauth_required", error_message=err_msg)
                raise PermissionError(
                    f"Authentication failed for '{acc.get('channel_title', account_id)}': {err_msg}"
                )

            data = resp.json()
            new_access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            new_refresh_token = data.get("refresh_token") or refresh_token

            db.update_account_tokens(
                account_id=account_id,
                access_token=new_access_token,
                expires_at=time.time() + int(expires_in),
                refresh_token=new_refresh_token,
            )
            db.update_account_status(account_id, "active", error_message="")
            return new_access_token

    async def get_valid_access_token_for_account(self, account_id: str) -> str:
        """
        Retrieves a valid access token for the specified account ID.
        Auto-refreshes using that account's own refresh token if expired.
        Guarantees strict token isolation between accounts.
        """
        acc = db.get_account(account_id)
        if not acc:
            raise PermissionError(f"YouTube account '{account_id}' is not connected. Please connect your account.")

        # Check if token is expired (or expires in less than 60 seconds)
        if time.time() >= (acc.get("expires_at", 0) - 60):
            return await self.force_refresh_account_token(account_id)

        return acc["access_token"]

    async def verify_account_channel(self, account_id: str) -> Dict[str, Any]:
        """
        Re-queries the YouTube Data API for an account to check if a YouTube channel has been created.
        Updates the account record if channel is found.
        """
        acc = db.get_account(account_id)
        if not acc:
            raise ValueError(f"Account '{account_id}' not found.")

        access_token = await self.get_valid_access_token_for_account(account_id)
        async with httpx.AsyncClient(timeout=15.0) as client:
            channel_info = await self.fetch_channel_info(client, access_token)

        if channel_info.get("has_channel"):
            channel_id = channel_info["id"]
            channel_title = channel_info["title"]
            channel_thumbnail = channel_info.get("thumbnail") or ""
            new_account_id = f"acc_{channel_id}"

            if new_account_id != account_id:
                db.delete_account(account_id)

            db.save_account(
                account_id=new_account_id,
                channel_id=channel_id,
                channel_title=channel_title,
                channel_thumbnail=channel_thumbnail,
                access_token=access_token,
                refresh_token=acc.get("refresh_token"),
                expires_at=acc.get("expires_at", time.time() + 3600),
                status="active",
                google_email=acc.get("google_email"),
            )
            return {
                "has_channel": True,
                "account_id": new_account_id,
                "channel_id": channel_id,
                "channel_title": channel_title,
                "channel_thumbnail": channel_thumbnail,
                "message": f"YouTube channel '{channel_title}' successfully verified!",
            }

        return {
            "has_channel": False,
            "account_id": account_id,
            "message": channel_info.get("message") or "Still no YouTube channel found for this account.",
        }

    async def disconnect_account(self, account_id: str) -> bool:
        """Revokes tokens with Google and removes account from persistent database."""
        acc = db.get_account(account_id)
        if not acc:
            return False

        # Attempt to revoke token with Google
        token_to_revoke = acc.get("access_token") or acc.get("refresh_token")
        if token_to_revoke:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(self.GOOGLE_REVOKE_URL, params={"token": token_to_revoke})
            except Exception:
                pass

        # Clear from session cache
        with self._lock:
            sessions_to_remove = [k for k, v in self._sessions.items() if v.get("account_id") == account_id]
            for s in sessions_to_remove:
                self._sessions.pop(s, None)

        # Delete from SQLite
        return db.delete_account(account_id)

    # -------------------------------------------------------------------------
    # LEGACY / SESSION COMPATIBILITY METHODS (PRESERVES EXISTING MODE 1)
    # -------------------------------------------------------------------------

    async def get_valid_access_token(self, session_id: str) -> str:
        """
        Retrieves a valid access token for session.
        Falls back to active account in SQLite to support restarts seamlessly.
        """
        with self._lock:
            session = self._sessions.get(session_id)

        if session and session.get("account_id"):
            return await self.get_valid_access_token_for_account(session["account_id"])

        if session:
            # In-memory session token refresh
            if time.time() >= (session.get("expires_at", 0) - 60):
                refresh_token = session.get("refresh_token")
                if not refresh_token:
                    raise PermissionError("Access token expired and no refresh token available. Please reconnect.")

                config = self._get_config()
                payload = {
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                }

                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(self.GOOGLE_TOKEN_URL, data=payload)
                    if resp.status_code != 200:
                        with self._lock:
                            self._sessions.pop(session_id, None)
                        raise PermissionError("Failed to refresh YouTube access token. Please reconnect your account.")

                    data = resp.json()
                    new_access_token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)

                    with self._lock:
                        if session_id in self._sessions:
                            self._sessions[session_id]["access_token"] = new_access_token
                            self._sessions[session_id]["expires_at"] = time.time() + int(expires_in)
                            self._sessions[session_id]["updated_at"] = time.time()

                    return new_access_token

            return session["access_token"]

        # If session not in memory, fallback to active account in SQLite
        active_acc = db.get_active_account()
        if active_acc:
            return await self.get_valid_access_token_for_account(active_acc["account_id"])

        raise PermissionError("No connected YouTube account found. Please connect your account first.")

    async def get_status(self, session_id: str) -> Dict[str, Any]:
        """Returns connection status, checking session and fallback active account."""
        with self._lock:
            session = self._sessions.get(session_id)

        if session:
            has_channel = session.get("has_channel", False)
            return {
                "authenticated": True,
                "account_id": session.get("account_id"),
                "has_channel": has_channel,
                "channel_title": session.get("channel_title"),
                "channel_id": session.get("channel_id"),
                "channel_thumbnail": session.get("channel_thumbnail"),
                "google_email": session.get("google_email"),
                "warning": None if has_channel else "No YouTube channel associated with this account.",
            }

        # Check SQLite active account
        active_acc = db.get_active_account()
        if active_acc:
            is_active_status = active_acc.get("status") == "active"
            return {
                "authenticated": True,
                "account_id": active_acc.get("account_id"),
                "has_channel": is_active_status,
                "channel_title": active_acc.get("channel_title"),
                "channel_id": active_acc.get("channel_id"),
                "channel_thumbnail": active_acc.get("channel_thumbnail"),
                "google_email": active_acc.get("google_email"),
                "warning": None if is_active_status else "Account requires reauthorization.",
            }

        return {
            "authenticated": False,
            "account_id": None,
            "has_channel": False,
            "channel_title": None,
            "channel_id": None,
            "channel_thumbnail": None,
            "google_email": None,
            "warning": None,
        }

    async def logout(self, session_id: str) -> bool:
        """Removes session from memory."""
        with self._lock:
            session = self._sessions.pop(session_id, None)

        if not session:
            return False

        token = session.get("access_token") or session.get("refresh_token")
        if token:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(self.GOOGLE_REVOKE_URL, params={"token": token})
            except Exception:
                pass

        return True


# Global singleton service instance
oauth_service = OAuthService()
