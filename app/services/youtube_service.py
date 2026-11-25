"""YouTube Data API v3, URL Validation, and Multi-Account Comment Publishing Service."""

import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse
import httpx

from app.services.database import db
from app.services.oauth_service import oauth_service


class YouTubeService:
    """Handles YouTube URL parsing, video metadata lookup, and account-specific comment publishing."""

    YOUTUBE_COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
    YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

    # Regex for standard 11-character YouTube video IDs
    VIDEO_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{11}$")

    # Allowed YouTube domains
    ALLOWED_DOMAINS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }

    def extract_video_id(self, url_or_id: str) -> str:
        """
        Extracts and validates YouTube Video ID from:
        - Raw 11-character video ID
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/shorts/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        """
        if not url_or_id or not isinstance(url_or_id, str):
            raise ValueError("Video URL or ID is required.")

        raw = url_or_id.strip()

        # Check if direct 11-character video ID
        if self.VIDEO_ID_REGEX.match(raw):
            return raw

        parsed = urlparse(raw)

        if parsed.scheme not in ("http", "https"):
            raise ValueError("Invalid URL scheme. URL must start with http:// or https://, or be a valid 11-character video ID.")

        domain = (parsed.hostname or "").lower()
        if domain not in self.ALLOWED_DOMAINS:
            raise ValueError(
                f"Unsupported domain '{domain}'. Only official YouTube URLs are accepted."
            )

        video_id: Optional[str] = None

        if domain == "youtu.be":
            path_parts = parsed.path.strip("/").split("/")
            if path_parts and path_parts[0]:
                video_id = path_parts[0]
        else:
            path = parsed.path.strip("/")
            if path == "watch":
                query_params = parse_qs(parsed.query)
                v_param = query_params.get("v")
                if v_param and v_param[0]:
                    video_id = v_param[0]
            elif path.startswith("shorts/"):
                parts = path.split("/")
                if len(parts) >= 2 and parts[1]:
                    video_id = parts[1]
            elif path.startswith("embed/"):
                parts = path.split("/")
                if len(parts) >= 2 and parts[1]:
                    video_id = parts[1]
            elif path.startswith("v/"):
                parts = path.split("/")
                if len(parts) >= 2 and parts[1]:
                    video_id = parts[1]

        if not video_id or not self.VIDEO_ID_REGEX.match(video_id):
            raise ValueError(
                "Could not extract a valid 11-character YouTube Video ID from the provided URL. "
                "Supported formats include youtube.com/watch?v=..., youtu.be/..., "
                "youtube.com/shorts/..., and youtube.com/embed/..."
            )

        return video_id

    async def get_video_details(self, video_url_or_id: str, access_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetches video title, description, thumbnail, and comment availability."""
        try:
            video_id = self.extract_video_id(video_url_or_id)
        except ValueError:
            return None

        headers: Dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        params = {"part": "snippet,statistics,status", "id": video_id}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self.YOUTUBE_VIDEOS_URL, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        snippet = items[0].get("snippet", {})
                        stats = items[0].get("statistics", {})
                        thumbs = snippet.get("thumbnails", {})
                        thumb_url = (
                            thumbs.get("high", {}).get("url")
                            or thumbs.get("medium", {}).get("url")
                            or thumbs.get("default", {}).get("url")
                            or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        )
                        # Check if commentCount is present or comments disabled
                        comments_disabled = "commentCount" not in stats and items[0].get("status", {}).get("privacyStatus") != "private"
                        return {
                            "video_id": video_id,
                            "title": snippet.get("title", ""),
                            "description": snippet.get("description", "")[:500],
                            "channel_title": snippet.get("channelTitle", ""),
                            "channel_id": snippet.get("channelId", ""),
                            "thumbnail_url": thumb_url,
                            "comments_available": not comments_disabled,
                            "view_count": stats.get("viewCount", "0"),
                            "comment_count": stats.get("commentCount", "N/A"),
                        }
        except Exception:
            pass

        # Fallback if API key/token not provided or call fails: return formatted standard dict
        return {
            "video_id": video_id,
            "title": f"YouTube Video ({video_id})",
            "description": "",
            "channel_title": "YouTube",
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "comments_available": True,
            "view_count": "N/A",
            "comment_count": "N/A",
        }

    async def publish_comment_for_account(
        self,
        account_id: str,
        video_url: str,
        comment_text: str,
        approved_comment: Optional[str] = None,
        run_id: Optional[str] = None,
        pdf_filename: Optional[str] = None,
        pdf_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Publishes a top-level comment strictly using the specified account's own verified OAuth token.
        Never cross-contaminates credentials between accounts. Logs result in SQLite.
        """
        # 1. Validate video URL & extract ID
        video_id = self.extract_video_id(video_url)

        # 2. Validate comment content
        if not comment_text or not comment_text.strip():
            raise ValueError("Comment text cannot be empty or only whitespace.")

        clean_comment = comment_text.strip()
        if len(clean_comment) > 10000:
            raise ValueError("Comment text exceeds maximum length of 10,000 characters.")

        final_approved = (approved_comment or clean_comment).strip()

        # 3. Retrieve account and verify connection
        account = db.get_account(account_id)
        if not account:
            raise PermissionError(f"Account '{account_id}' is not connected. Please connect your account first.")

        # 4. Retrieve valid, fresh access token for this specific account
        access_token = await oauth_service.get_valid_access_token_for_account(account_id)

        # 5. Verify the account has an active YouTube channel (with 401 auto-refresh)
        try:
            await self.verify_user_has_channel(access_token)
        except PermissionError as pe:
            if "401" in str(pe):
                access_token = await oauth_service.force_refresh_account_token(account_id)
                await self.verify_user_has_channel(access_token)
            else:
                raise

        # 6. Build YouTube commentThreads payload
        payload = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {
                        "textOriginal": final_approved
                    }
                }
            }
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self.YOUTUBE_COMMENT_THREADS_URL,
                params={"part": "snippet"},
                json=payload,
                headers=headers,
            )

            # Auto-handle 401 Unauthorized with forced token refresh and retry
            if resp.status_code == 401:
                try:
                    new_token = await oauth_service.force_refresh_account_token(account_id)
                    headers["Authorization"] = f"Bearer {new_token}"
                    resp = await client.post(
                        self.YOUTUBE_COMMENT_THREADS_URL,
                        params={"part": "snippet"},
                        json=payload,
                        headers=headers,
                    )
                except Exception as refresh_err:
                    err_msg = f"401 Unauthorized: Token refresh failed ({str(refresh_err)}). Please reconnect this account."
                    db.log_comment(
                        video_id=video_id,
                        video_title=video_url,
                        video_url=video_url,
                        assigned_comment=clean_comment,
                        approved_comment=final_approved,
                        account_id=account_id,
                        channel_id=account.get("channel_id"),
                        comment_source="pdf" if pdf_filename else "ai",
                        publishing_status="failed",
                        error_details=err_msg,
                        run_id=run_id,
                        pdf_filename=pdf_filename,
                        pdf_hash=pdf_hash,
                    )
                    db.update_account_status(account_id, "reauth_required", error_message=err_msg)
                    raise PermissionError(err_msg)

            if resp.status_code in (200, 201):
                res_data = resp.json()
                comment_id = res_data.get("id", "")
                snippet = res_data.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                author = snippet.get("authorDisplayName") or account.get("channel_title", "You")
                published_at = snippet.get("publishedAt", "")

                # Record success in database
                db.log_comment(
                    video_id=video_id,
                    video_title=video_url,
                    video_url=video_url,
                    assigned_comment=clean_comment,
                    approved_comment=final_approved,
                    account_id=account_id,
                    channel_id=account.get("channel_id"),
                    comment_source="pdf" if pdf_filename else "ai",
                    publishing_status="success",
                    published_comment_id=comment_id,
                    run_id=run_id,
                    pdf_filename=pdf_filename,
                    pdf_hash=pdf_hash,
                )

                return {
                    "status": "success",
                    "message": f"Comment successfully published from account '{account.get('channel_title')}'!",
                    "comment_id": comment_id,
                    "video_id": video_id,
                    "account_id": account_id,
                    "channel_title": account.get("channel_title"),
                    "author": author,
                    "published_at": published_at,
                    "raw_response": res_data,
                }

            # If 401 persists after retry:
            if resp.status_code == 401:
                err_msg = f"401 Unauthorized for account '{account.get('channel_title')}'. Please click 'Reconnect Account'."
                db.update_account_status(account_id, "reauth_required", error_message=err_msg)
                db.log_comment(
                    video_id=video_id,
                    video_title=video_url,
                    video_url=video_url,
                    assigned_comment=clean_comment,
                    approved_comment=final_approved,
                    account_id=account_id,
                    channel_id=account.get("channel_id"),
                    comment_source="pdf" if pdf_filename else "ai",
                    publishing_status="failed",
                    error_details=err_msg,
                    run_id=run_id,
                    pdf_filename=pdf_filename,
                    pdf_hash=pdf_hash,
                )
                raise PermissionError(err_msg)

            # Parse error and record failure in database
            error_msg = self._parse_youtube_error(resp)
            db.log_comment(
                video_id=video_id,
                video_title=video_url,
                video_url=video_url,
                assigned_comment=clean_comment,
                approved_comment=final_approved,
                account_id=account_id,
                channel_id=account.get("channel_id"),
                comment_source="pdf" if pdf_filename else "ai",
                publishing_status="failed",
                error_details=error_msg,
                run_id=run_id,
                pdf_filename=pdf_filename,
                pdf_hash=pdf_hash,
            )
            raise RuntimeError(error_msg)

    async def publish_comment(self, session_id: str, video_url: str, comment_text: str) -> Dict[str, Any]:
        """
        Legacy publish_comment method. Preserved for Mode 1 single-video workflow.
        Checks if session matches an account or uses active account.
        """
        if session_id.startswith("acc_"):
            return await self.publish_comment_for_account(
                account_id=session_id,
                video_url=video_url,
                comment_text=comment_text,
            )

        active_acc = db.get_active_account()
        if active_acc:
            return await self.publish_comment_for_account(
                account_id=active_acc["account_id"],
                video_url=video_url,
                comment_text=comment_text,
            )

        # Direct session token publish
        video_id = self.extract_video_id(video_url)
        clean_comment = comment_text.strip()
        access_token = await oauth_service.get_valid_access_token(session_id)
        await self.verify_user_has_channel(access_token)

        payload = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": clean_comment}}
            }
        }
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(self.YOUTUBE_COMMENT_THREADS_URL, params={"part": "snippet"}, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                res_data = resp.json()
                return {
                    "status": "success",
                    "message": "Comment successfully published to YouTube!",
                    "comment_id": res_data.get("id", ""),
                    "video_id": video_id,
                }
            raise RuntimeError(self._parse_youtube_error(resp))

    async def verify_user_has_channel(self, access_token: str) -> Dict[str, Any]:
        """
        Verifies that the authenticated account has an associated YouTube channel
        using channels.list with mine=true.
        """
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"part": "snippet", "mine": "true"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if not items:
                        raise RuntimeError(
                            "Your Google account is connected, but has no associated YouTube channel. "
                            "A YouTube channel is required to publish comments. "
                            "Please visit https://www.youtube.com/create_channel to create your channel, "
                            "then return here and click Refresh Status."
                        )
                    snippet = items[0].get("snippet", {})
                    return {
                        "channel_id": items[0].get("id", ""),
                        "channel_title": snippet.get("title", ""),
                    }
                elif resp.status_code == 401:
                    raise PermissionError("401 Unauthorized: Access token is invalid or expired.")
                elif resp.status_code == 403:
                    error_msg = self._parse_youtube_error(resp)
                    raise RuntimeError(error_msg)
                else:
                    raise RuntimeError(f"Could not verify YouTube channel status ({resp.status_code}): {resp.text}")
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error while verifying YouTube channel: {str(e)}")

    def _parse_youtube_error(self, resp: httpx.Response) -> str:
        """Extracts human-readable error reasons from YouTube Data API errors."""
        try:
            error_json = resp.json()
            error_obj = error_json.get("error", {})
            errors = error_obj.get("errors", [])

            if errors:
                first_err = errors[0]
                reason = first_err.get("reason", "")
                message = first_err.get("message", "")

                if reason == "ineligibleAccount" or "not connected to google+" in message.lower() or "ineligibleaccount" in message.lower():
                    return (
                        "YouTube API Error (ineligibleAccount): The authenticated Google account is not connected to a YouTube channel. "
                        "A YouTube channel is required to publish comments. "
                        "Please visit https://www.youtube.com/create_channel to create your channel handle and profile, then try again."
                    )
                if reason == "commentsDisabled":
                    return "Comments are disabled for this video by the creator."
                if reason == "insufficientPermissions" or (resp.status_code == 403 and "scope" in message.lower()):
                    return "Insufficient permissions to publish comments. Please reconnect your YouTube account."
                if reason == "quotaExceeded":
                    return "YouTube API daily quota exceeded. Please try again tomorrow or use another project."
                if reason == "videoNotFound":
                    return "The requested video was not found or is set to private."
                if reason == "commentContainsRestrictedContent":
                    return "YouTube rejected the comment because it contains restricted or prohibited content."
                if reason == "forbidden":
                    return f"Action forbidden by YouTube: {message or reason}"
                if message:
                    return f"YouTube API Error ({reason or resp.status_code}): {message}"

            msg = error_obj.get("message")
            if msg:
                return f"YouTube API Error ({resp.status_code}): {msg}"
        except Exception:
            pass

        if resp.status_code == 401:
            return "YouTube authorization expired or invalid. Please disconnect and reconnect your account."
        if resp.status_code == 403:
            return "Access forbidden. Ensure comments are enabled and your account has permission."
        if resp.status_code == 404:
            return "Video not found on YouTube."

        return f"YouTube API request failed with status {resp.status_code}: {resp.text}"


# Global singleton service instance
youtube_service = YouTubeService()
