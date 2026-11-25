"""YouTube Channel Resolution and Video Retrieval Service."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import httpx


class YouTubeChannelService:
    """Handles channel URL resolution and video retrieval via YouTube Data API v3."""

    YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
    YOUTUBE_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
    YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

    CHANNEL_ID_REGEX = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")

    def parse_channel_input(self, channel_input: str) -> Tuple[str, str]:
        """
        Parses user channel input and returns (identifier_type, value).
        identifier_type can be: 'handle', 'id', 'username', 'custom'
        """
        if not channel_input or not isinstance(channel_input, str):
            raise ValueError("Channel URL or handle is required.")

        raw = channel_input.strip()

        # Direct handle: e.g. "@username"
        if raw.startswith("@"):
            return "handle", raw[1:]

        # Direct Channel ID: e.g. "UC..."
        if self.CHANNEL_ID_REGEX.match(raw):
            return "id", raw

        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        domain = (parsed.hostname or "").lower()

        allowed_domains = {"youtube.com", "www.youtube.com", "m.youtube.com"}
        if domain not in allowed_domains:
            raise ValueError(f"Unsupported domain '{domain}'. Please provide a valid YouTube channel URL.")

        path = parsed.path.strip("/")
        parts = path.split("/")

        if not parts or not parts[0]:
            raise ValueError("Invalid YouTube channel URL.")

        # Handle URL formats:
        # 1. /@handle
        if parts[0].startswith("@"):
            return "handle", parts[0][1:]

        # 2. /channel/UC...
        if parts[0] == "channel" and len(parts) >= 2:
            return "id", parts[1]

        # 3. /c/customname
        if parts[0] == "c" and len(parts) >= 2:
            return "custom", parts[1]

        # 4. /user/username
        if parts[0] == "user" and len(parts) >= 2:
            return "username", parts[1]

        # Fallback: single word path might be a handle or custom name
        return "handle", parts[0]

    async def get_channel_details(
        self,
        channel_input: str,
        access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Resolves channel details, channel ID, and uploads playlist ID."""
        id_type, val = self.parse_channel_input(channel_input)

        headers: Dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        api_key = os.getenv("GOOGLE_API_KEY", "")

        # No credentials at all -> use the public (keyless) web fallback
        if not api_key and not access_token:
            return await self._scrape_channel_details(channel_input, id_type, val)

        try:
            return await self._get_channel_details_api(channel_input, id_type, val, access_token, headers, api_key)
        except (RuntimeError, ValueError):
            # API failed (quota, invalid key, not found) -> try keyless fallback
            try:
                return await self._scrape_channel_details(channel_input, id_type, val)
            except Exception:
                raise

    async def _scrape_channel_details(self, channel_input: str, id_type: str, val: str) -> Dict[str, Any]:
        """Resolve channel ID/title from the public channel page (no API key needed)."""
        if id_type == "id":
            url = f"https://www.youtube.com/channel/{val}"
        elif id_type == "handle":
            url = f"https://www.youtube.com/@{val}"
        elif id_type == "username":
            url = f"https://www.youtube.com/user/{val}"
        else:
            url = f"https://www.youtube.com/c/{val}"

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=self._WEB_HEADERS, cookies={"CONSENT": "YES+1"})
        if resp.status_code != 200:
            raise ValueError(f"Could not find YouTube channel for input: '{channel_input}'. Please check the URL or handle.")

        html = resp.text
        m = (
            re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html)
            or re.search(r'"channelId":"(UC[\w-]{22})"', html)
            or re.search(r'"externalId":"(UC[\w-]{22})"', html)
        )
        if not m:
            raise ValueError(f"Could not find YouTube channel for input: '{channel_input}'. Please check the URL or handle.")
        channel_id = m.group(1)
        t = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        img = re.search(r'<meta property="og:image" content="([^"]*)"', html)
        return {
            "channel_id": channel_id,
            "title": t.group(1) if t else channel_id,
            "description": "",
            "custom_url": f"@{val}" if id_type == "handle" else "",
            "thumbnail_url": img.group(1) if img else "",
            "uploads_playlist_id": "UU" + channel_id[2:],
            "video_count": 0,
            "subscriber_count": "N/A",
        }

    async def _scrape_channel_videos(self, uploads_playlist_id: str, max_videos: int) -> List[Dict[str, Any]]:
        """Fetch videos without an API key: playlist page (ytInitialData) with RSS feed fallback."""
        import json as _json
        videos: List[Dict[str, Any]] = []
        seen = set()

        def add(vid: str, title: str, published: str = ""):
            if vid and vid not in seen and len(videos) < max_videos:
                seen.add(vid)
                videos.append({
                    "video_id": vid,
                    "title": title or f"Video {vid}",
                    "description": "",
                    "published_at": published,
                    "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={vid}",
                })

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            # 1) Playlist page (typically ~100 videos)
            try:
                resp = await client.get(
                    f"https://www.youtube.com/playlist?list={uploads_playlist_id}",
                    headers=self._WEB_HEADERS, cookies={"CONSENT": "YES+1"},
                )
                m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});</script>", resp.text, re.S)
                if m:
                    data = _json.loads(m.group(1))

                    def walk(o):
                        if isinstance(o, dict):
                            r = o.get("playlistVideoRenderer")
                            if r and r.get("videoId"):
                                runs = r.get("title", {}).get("runs", [])
                                add(r["videoId"], runs[0].get("text", "") if runs else "")
                            lk = o.get("lockupViewModel")
                            if isinstance(lk, dict) and lk.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO" and lk.get("contentId"):
                                ttl = lk.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {}).get("content", "")
                                add(lk["contentId"], ttl)
                            for v in o.values():
                                walk(v)
                        elif isinstance(o, list):
                            for v in o:
                                walk(v)
                    walk(data)
            except Exception:
                pass

            # 2) RSS feed fallback (latest 15)
            if not videos:
                channel_id = "UC" + uploads_playlist_id[2:]
                resp = await client.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=self._WEB_HEADERS)
                if resp.status_code == 200:
                    for e in re.finditer(r"<entry>.*?<yt:videoId>([\w-]{11})</yt:videoId>.*?<title>(.*?)</title>.*?<published>(.*?)</published>", resp.text, re.S):
                        title = e.group(2).replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
                        add(e.group(1), title, e.group(3))
        return videos

    _WEB_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def _get_channel_details_api(
        self, channel_input: str, id_type: str, val: str,
        access_token: Optional[str], headers: Dict[str, str], api_key: str,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "part": "snippet,contentDetails,statistics",
        }
        if api_key and not access_token:
            params["key"] = api_key

        if id_type == "id":
            params["id"] = val
        elif id_type == "handle":
            params["forHandle"] = val
        elif id_type == "username":
            params["forUsername"] = val
        else:
            # Custom name fallback: try forHandle first, then search
            params["forHandle"] = val

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.YOUTUBE_CHANNELS_URL, params=params, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    return self._format_channel_info(items[0])

            # If forHandle failed or returned 0 items, and it was a custom name or handle, try search fallback
            if id_type in ("handle", "custom"):
                search_params = {
                    "part": "snippet",
                    "type": "channel",
                    "q": val,
                    "maxResults": 1,
                }
                if api_key and not access_token:
                    search_params["key"] = api_key

                search_resp = await client.get(self.YOUTUBE_SEARCH_URL, params=search_params, headers=headers)
                if search_resp.status_code == 200:
                    s_data = search_resp.json()
                    s_items = s_data.get("items", [])
                    if s_items:
                        found_id = s_items[0].get("id", {}).get("channelId")
                        if found_id:
                            # Re-query channel by ID
                            c_params = {"part": "snippet,contentDetails,statistics", "id": found_id}
                            if api_key and not access_token:
                                c_params["key"] = api_key
                            c_resp = await client.get(self.YOUTUBE_CHANNELS_URL, params=c_params, headers=headers)
                            if c_resp.status_code == 200:
                                c_items = c_resp.json().get("items", [])
                                if c_items:
                                    return self._format_channel_info(c_items[0])

            # If error response
            if resp.status_code == 403:
                err_text = self._parse_error(resp)
                raise RuntimeError(err_text)

            raise ValueError(f"Could not find YouTube channel for input: '{channel_input}'. Please check the URL or handle.")

    def _format_channel_info(self, item: Dict[str, Any]) -> Dict[str, Any]:
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        statistics = item.get("statistics", {})

        channel_id = item.get("id", "")

        # Uploads playlist ID is either in contentDetails or derived by replacing 'UC' with 'UU'
        uploads_playlist = (
            content_details.get("relatedPlaylists", {}).get("uploads")
            or (f"UU{channel_id[2:]}" if channel_id.startswith("UC") else "")
        )

        thumbnails = snippet.get("thumbnails", {})
        thumb_url = (
            thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
            or ""
        )

        return {
            "channel_id": channel_id,
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "custom_url": snippet.get("customUrl", ""),
            "thumbnail_url": thumb_url,
            "uploads_playlist_id": uploads_playlist,
            "video_count": int(statistics.get("videoCount", 0)),
            "subscriber_count": statistics.get("subscriberCount", "N/A"),
        }

    async def get_channel_videos(
        self,
        uploads_playlist_id: str,
        max_videos: int = 100,
        access_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves public uploaded videos from the channel's uploads playlist using pagination.
        Filters out private, deleted, or inaccessible videos.
        """
        if not uploads_playlist_id:
            raise ValueError("Uploads playlist ID is required to fetch channel videos.")

        headers: Dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        api_key = os.getenv("GOOGLE_API_KEY", "")

        if not api_key and not access_token:
            return await self._scrape_channel_videos(uploads_playlist_id, max_videos)

        videos: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None

        async with httpx.AsyncClient(timeout=20.0) as client:
            while len(videos) < max_videos:
                batch_size = min(50, max_videos - len(videos))
                params: Dict[str, Any] = {
                    "part": "snippet",
                    "playlistId": uploads_playlist_id,
                    "maxResults": batch_size,
                }
                if next_page_token:
                    params["pageToken"] = next_page_token
                if api_key and not access_token:
                    params["key"] = api_key

                resp = await client.get(self.YOUTUBE_PLAYLIST_ITEMS_URL, params=params, headers=headers)

                if resp.status_code != 200:
                    err_msg = self._parse_error(resp)
                    # If some videos were already fetched, return what we have instead of failing completely
                    if videos:
                        break
                    fallback = await self._scrape_channel_videos(uploads_playlist_id, max_videos)
                    if fallback:
                        return fallback
                    raise RuntimeError(f"Failed to fetch videos from channel ({resp.status_code}): {err_msg}")

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    snippet = item.get("snippet", {})
                    video_id = snippet.get("resourceId", {}).get("videoId")
                    title = snippet.get("title", "")

                    # Skip private or deleted videos
                    if not video_id or title in ("Private video", "Deleted video"):
                        continue

                    thumbnails = snippet.get("thumbnails", {})
                    thumb_url = (
                        thumbnails.get("medium", {}).get("url")
                        or thumbnails.get("default", {}).get("url")
                        or ""
                    )

                    videos.append({
                        "video_id": video_id,
                        "title": title,
                        "description": snippet.get("description", "")[:200],
                        "published_at": snippet.get("publishedAt", ""),
                        "thumbnail_url": thumb_url,
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                    })

                    if len(videos) >= max_videos:
                        break

                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break

        return videos

    def _parse_error(self, resp: httpx.Response) -> str:
        try:
            data = resp.json()
            err = data.get("error", {})
            msg = err.get("message")
            errors = err.get("errors", [])
            if errors and errors[0].get("reason") == "quotaExceeded":
                return "YouTube API daily quota exceeded. Please try again tomorrow or use another project."
            if msg:
                return msg
        except Exception:
            pass
        return resp.text


# Global channel service instance
youtube_channel_service = YouTubeChannelService()
