"""YouTube AI & Multi-Account PDF Comment Management System - Streamlit Dashboard."""

import asyncio
import io
import os
import time
import uuid
from typing import Any, Dict, List, Optional
import pandas as pd
from dotenv import load_dotenv
import httpx
import streamlit as st

# Load environment configuration
load_dotenv()

from app.services.comment_assignment import comment_assignment_engine
from app.services.comment_publisher import comment_publisher
from app.services.database import db
from app.services.oauth_service import oauth_service
from app.services.pdf_extractor import pdf_extractor
from app.services.youtube_channel_service import youtube_channel_service
from app.services.youtube_service import youtube_service

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(
    page_title="YouTube AI & Multi-Account PDF Comment Agent",
    page_icon="▶",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# 1. SESSION STATE INITIALIZATION
# -----------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "selected_account_id" not in st.session_state:
    st.session_state.selected_account_id = None

if "auth_url" not in st.session_state:
    st.session_state.auth_url = None

if "message" not in st.session_state:
    st.session_state.message = None  # ("success" | "error" | "info" | "warning", text)

# Mode 1 State
if "mode1_comment_text" not in st.session_state:
    st.session_state.mode1_comment_text = ""

# Mode 2 & Multi-Account PDF State
if "pdf_data" not in st.session_state:
    st.session_state.pdf_data = None

if "draft_comments" not in st.session_state:
    st.session_state.draft_comments = []

if "target_videos" not in st.session_state:
    st.session_state.target_videos = []

if "assignments_list" not in st.session_state:
    st.session_state.assignments_list = []

if "pub_job_active" not in st.session_state:
    st.session_state.pub_job_active = False

if "pub_job_paused" not in st.session_state:
    st.session_state.pub_job_paused = False

if "pub_job_stopped" not in st.session_state:
    st.session_state.pub_job_stopped = False


# -----------------------------------------------------------------------------
# 2. BACKEND & SERVICE API HELPERS
# -----------------------------------------------------------------------------
def fetch_accounts() -> List[Dict[str, Any]]:
    """Retrieves list of connected YouTube accounts from SQLite or Backend."""
    try:
        with httpx.Client(base_url=BACKEND_URL, timeout=4.0) as client:
            resp = client.get("/auth/accounts")
            if resp.status_code == 200:
                return resp.json().get("accounts", [])
    except Exception:
        pass
    # Fallback to direct service call if running in unified process
    return oauth_service.list_accounts()


def get_active_account_info() -> Optional[Dict[str, Any]]:
    accounts = fetch_accounts()
    for acc in accounts:
        if acc.get("is_active"):
            return acc
    return accounts[0] if accounts else None


def set_active_account_api(account_id: str) -> bool:
    try:
        with httpx.Client(base_url=BACKEND_URL, timeout=5.0) as client:
            resp = client.post("/auth/accounts/activate", json={"account_id": account_id})
            return resp.status_code == 200
    except Exception:
        return oauth_service.set_active_account(account_id)


def disconnect_account_api(account_id: str) -> bool:
    try:
        with httpx.Client(base_url=BACKEND_URL, timeout=8.0) as client:
            resp = client.post("/auth/accounts/disconnect", json={"account_id": account_id})
            return resp.status_code == 200
    except Exception:
        return asyncio.run(oauth_service.disconnect_account(account_id))


def get_oauth_auth_url(target_account_id: Optional[str] = None) -> Optional[str]:
    try:
        params = {
            "session_id": st.session_state.session_id,
            "prompt": "select_account consent",
        }
        if target_account_id:
            params["target_account_id"] = target_account_id
        with httpx.Client(base_url=BACKEND_URL, timeout=5.0) as client:
            resp = client.get("/auth/youtube", params=params)
            if resp.status_code == 200:
                return resp.json().get("auth_url")
    except Exception as e:
        st.session_state.message = ("error", f"Failed to generate Google OAuth URL: {str(e)}")
    return None


def verify_account_channel_api(account_id: str) -> Dict[str, Any]:
    try:
        with httpx.Client(base_url=BACKEND_URL, timeout=15.0) as client:
            resp = client.post(f"/auth/accounts/{account_id}/verify-channel")
            if resp.status_code == 200:
                return resp.json()
            return {"has_channel": False, "message": resp.text}
    except Exception as e:
        return {"has_channel": False, "message": str(e)}


# -----------------------------------------------------------------------------
# 3. HEADER & GLOBAL METRICS DASHBOARD
# -----------------------------------------------------------------------------
connected_accounts = fetch_accounts()
active_acc = get_active_account_info()
db_stats = db.get_stats()

st.title("YouTube AI Commenting Agent")
st.caption("Enterprise Multi-Account Automation Platform: AI Single Video Commenting & Multi-Account PDF Workflow")

# Display global messages
if st.session_state.message:
    m_type, m_text = st.session_state.message
    if m_type == "success":
        st.success(m_text)
    elif m_type == "error":
        st.error(m_text)
    elif m_type == "warning":
        st.warning(m_text)
    else:
        st.info(m_text)
    st.session_state.message = None

# KPI Metric Row
m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)
with m_col1:
    st.metric("Connected Accounts", len(connected_accounts), help="Total authorized YouTube accounts stored")
with m_col2:
    active_title = active_acc.get("channel_title")[:14] + "..." if active_acc and len(active_acc.get("channel_title", "")) > 14 else (active_acc.get("channel_title") if active_acc else "None")
    st.metric("Active Account", active_title or "None")
with m_col3:
    st.metric("Extracted Comments", len(st.session_state.draft_comments))
with m_col4:
    pending_count = sum(1 for a in st.session_state.assignments_list if a.get("status") == "pending" and not a.get("is_approved"))
    st.metric("Awaiting Approval", pending_count)
with m_col5:
    st.metric("Published Comments", db_stats.get("total_success", 0))
with m_col6:
    st.metric("Unused Comments", len(st.session_state.draft_comments) - len(st.session_state.assignments_list) if len(st.session_state.draft_comments) > len(st.session_state.assignments_list) else 0)

st.divider()

# -----------------------------------------------------------------------------
# 4. TAB NAVIGATION (6 TABS)
# -----------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🎯 Single Video AI Commenting",
    "👥 Multi-Account Management",
    "📄 PDF Comment Management",
    "📋 Comment Review & Publishing",
    "📊 Account-Wise History",
    "⚙️ Settings & Diagnostics",
])


# =============================================================================
# TAB 1: SINGLE VIDEO AI COMMENTING (MODE 1 - FULLY PRESERVED EXISTING)
# =============================================================================
with tab1:
    st.subheader("Mode 1: Single Video AI Commenting")
    st.write("Generate an engaging, context-aware comment using AI and publish it from any connected YouTube account.")

    # Account Selector
    if connected_accounts:
        acc_options = {acc["account_id"]: f"{acc['channel_title']} ({acc['channel_id']})" for acc in connected_accounts}
        default_index = 0
        if active_acc and active_acc["account_id"] in acc_options:
            default_index = list(acc_options.keys()).index(active_acc["account_id"])
        selected_mode1_acc_id = st.selectbox(
            "Publishing Account",
            options=list(acc_options.keys()),
            format_func=lambda x: acc_options[x],
            index=default_index,
            key="mode1_account_select",
        )
    else:
        st.warning("⚠️ No YouTube account connected yet. Go to **Tab 2: Multi-Account Management** to connect an account.")
        selected_mode1_acc_id = None

    col_vurl, col_btn_vinfo = st.columns([4, 1])
    with col_vurl:
        video_url_input = st.text_input(
            "YouTube Video URL or Video ID",
            placeholder="https://www.youtube.com/watch?v=VIDEO_ID or VIDEO_ID",
            help="Supports standard watch?v=, youtu.be, shorts/, embed/, or direct 11-char IDs.",
            key="mode1_video_url",
        )
    with col_btn_vinfo:
        st.write("")
        st.write("")
        inspect_vid = st.button("Preview Video", key="mode1_inspect_vid")

    # Video preview card if URL provided
    if video_url_input and (inspect_vid or "mode1_vinfo" in st.session_state):
        try:
            v_id = youtube_service.extract_video_id(video_url_input)
            v_info = asyncio.run(youtube_service.get_video_details(v_id))
            st.session_state.mode1_vinfo = v_info
            if v_info:
                with st.container():
                    c_thumb, c_meta = st.columns([1, 3])
                    with c_thumb:
                        if v_info.get("thumbnail_url"):
                            st.image(v_info["thumbnail_url"], use_container_width=True)
                    with c_meta:
                        st.markdown(f"**{v_info.get('title')}**")
                        st.caption(f"Channel: {v_info.get('channel_title')} | ID: `{v_info.get('video_id')}`")
                        if not v_info.get("comments_available", True):
                            st.error("⚠️ Note: Comments appear to be disabled on this video.")
        except Exception as e:
            st.warning(f"Could not load video preview: {str(e)}")

    with st.expander("AI Instructions & Tone (Optional)", expanded=False):
        ai_instruction = st.text_input(
            "Instructions / Desired Tone",
            placeholder="e.g. Congratulatory, insightful, asking a question about the topic...",
            key="mode1_ai_prompt",
        )

    col_gen, col_clear = st.columns([1, 4])
    with col_gen:
        if st.button("Generate AI Comment", type="secondary", key="mode1_gen_btn"):
            if not video_url_input or not video_url_input.strip():
                st.error("Please enter a YouTube video URL first.")
            else:
                try:
                    with st.spinner("Generating relevant comment with AI..."):
                        with httpx.Client(base_url=BACKEND_URL, timeout=30.0) as client:
                            resp = client.post(
                                "/comment/generate",
                                json={
                                    "account_id": selected_mode1_acc_id,
                                    "session_id": st.session_state.session_id,
                                    "video_url": video_url_input.strip(),
                                    "prompt": ai_instruction.strip() if ai_instruction else None,
                                },
                            )
                            if resp.status_code == 200:
                                gen_comment = resp.json().get("comment", "")
                                st.session_state.mode1_comment_text = gen_comment
                                st.session_state.message = ("success", "AI comment generated! Review and edit below.")
                                st.rerun()
                            else:
                                err_detail = resp.json().get("detail", resp.text)
                                st.error(f"AI Generation failed: {err_detail}")
                except Exception as e:
                    st.error(f"Failed to communicate with backend: {str(e)}")

    # Editable comment text area
    comment_area = st.text_area(
        "Comment Text (Review & Edit before publishing)",
        value=st.session_state.mode1_comment_text,
        height=130,
        placeholder="Type a comment manually or click Generate AI Comment above...",
        key="mode1_comment_area",
    )
    st.session_state.mode1_comment_text = comment_area

    # Explicit Human Approval & Publishing Button
    col_pub, col_stat = st.columns([1, 2])
    with col_pub:
        publish_btn = st.button("🚀 Publish Comment", type="primary", use_container_width=True, key="mode1_publish_btn")

    if publish_btn:
        if not selected_mode1_acc_id:
            st.error("Please connect and select a YouTube account first.")
        elif not video_url_input or not video_url_input.strip():
            st.error("Please enter a valid YouTube video URL.")
        elif not comment_area or not comment_area.strip():
            st.error("Comment text cannot be empty.")
        else:
            try:
                with st.spinner("Publishing comment via official YouTube Data API..."):
                    with httpx.Client(base_url=BACKEND_URL, timeout=30.0) as client:
                        resp = client.post(
                            "/comment/publish",
                            json={
                                "account_id": selected_mode1_acc_id,
                                "session_id": st.session_state.session_id,
                                "video_url": video_url_input.strip(),
                                "comment": comment_area.strip(),
                                "approved_comment": comment_area.strip(),
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            c_id = data.get("comment_id", "")
                            st.success(f"✅ Comment published successfully! Comment ID: `{c_id}`")
                            st.session_state.mode1_comment_text = ""
                            st.rerun()
                        else:
                            err_detail = resp.json().get("detail", resp.text)
                            st.error(f"Publishing failed: {err_detail}")
            except Exception as e:
                st.error(f"Request failed: {str(e)}")


# =============================================================================
# TAB 2: MULTI-ACCOUNT MANAGEMENT (CENTRALIZED GOOGLE OAUTH)
# =============================================================================
with tab2:
    st.subheader("Multi-Account Google & YouTube Management")
    st.write(
        "Connect, view, switch, and reconnect multiple YouTube channels via centralized Google OAuth 2.0. "
        "All accounts use ONE centralized Google Client ID and Secret with independent, AES-encrypted tokens."
    )

    st.info(
        "🔐 **Centralized OAuth Architecture**: A single `GOOGLE_CLIENT_ID` in your `.env` manages all Google accounts. "
        "Each account maintains its own isolated access and refresh tokens, securely encrypted at rest. "
        "You never need separate Google Cloud projects or separate client IDs."
    )

    col_btn_conn, col_btn_ref = st.columns([1, 4])
    with col_btn_conn:
        if st.button("➕ Connect Google Account", type="primary", use_container_width=True):
            auth_url = get_oauth_auth_url(target_account_id=None)
            if auth_url:
                st.session_state.auth_url = auth_url
                st.session_state.reauth_target_name = None
                st.rerun()

    with col_btn_ref:
        if st.button("🔄 Refresh Accounts", use_container_width=False):
            st.rerun()

    # If Auth URL generated, show connection banner
    if st.session_state.auth_url:
        target_note = f" for '{st.session_state.reauth_target_name}'" if st.session_state.get("reauth_target_name") else ""
        st.info(f"👇 Click the button below to authorize YouTube account{target_note} with Google:")
        st.link_button(
            "👉 Open Google Account Authorization Window",
            url=st.session_state.auth_url,
            type="primary",
        )
        st.caption("A new Google window will open. Select the Google account with your active YouTube channel. After authorizing, return here and click **Refresh Accounts**.")

    st.write("---")

    # Display Accounts Cards
    if not connected_accounts:
        st.info("No YouTube accounts currently connected. Click **Connect Google Account** above to link your first YouTube channel.")
    else:
        st.write(f"**Connected Accounts ({len(connected_accounts)})**")
        for acc in connected_accounts:
            with st.container():
                card_c1, card_c2, card_c3, card_c4 = st.columns([1, 4, 2, 3])

                with card_c1:
                    if acc.get("channel_thumbnail"):
                        st.image(acc["channel_thumbnail"], width=64)
                    else:
                        st.markdown("👤")

                with card_c2:
                    st.markdown(f"### {acc.get('channel_title')}")
                    meta_parts = []
                    if acc.get("channel_id"):
                        meta_parts.append(f"Channel ID: `{acc.get('channel_id')}`")
                    if acc.get("google_email"):
                        meta_parts.append(f"Google: **{acc.get('google_email')}**")
                    meta_parts.append(f"Account ID: `{acc.get('account_id')}`")
                    st.caption(" | ".join(meta_parts))
                    if acc.get("is_active"):
                        st.success("🌟 Active Default Account")
                    if acc.get("error_message"):
                        st.error(f"⚠️ {acc.get('error_message')}")

                with card_c3:
                    acc_status = acc.get("status", "active")
                    if acc_status == "active":
                        st.markdown("🟢 **Connection**: Connected")
                        st.markdown("🔒 **Auth**: Active (Encrypted)")
                    elif acc_status == "reauth_required":
                        st.markdown("⚠️ **Connection**: Action Required")
                        st.markdown("🔴 **Auth**: Reconnect Required")
                    elif acc_status == "no_channel":
                        st.markdown("⚠️ **Connection**: No Channel")
                        st.markdown("⚪ **Auth**: Channel Missing")
                    else:
                        st.markdown(f"⚪ **Status**: {acc_status.title()}")
                    st.caption(f"Last Authorized: {acc.get('last_auth_at', 'N/A')[:19]}")

                with card_c4:
                    col_act1, col_act2, col_act3 = st.columns(3)
                    with col_act1:
                        if not acc.get("is_active") and acc.get("status") == "active":
                            if st.button("⭐ Set Active", key=f"btn_act_{acc['account_id']}"):
                                set_active_account_api(acc["account_id"])
                                st.session_state.message = ("success", f"Set '{acc['channel_title']}' as default active account.")
                                st.rerun()
                        elif acc.get("status") != "active":
                            if st.button("🔍 Verify", key=f"btn_vfy_{acc['account_id']}", help="Check if channel was created on YouTube"):
                                v_res = verify_account_channel_api(acc["account_id"])
                                if v_res.get("has_channel"):
                                    st.session_state.message = ("success", v_res.get("message", "Channel verified!"))
                                else:
                                    st.session_state.message = ("warning", v_res.get("message", "No channel found."))
                                st.rerun()
                    with col_act2:
                        reauth_label = "🔄 Reconnect" if acc.get("status") == "reauth_required" else "🔄 Reconnect"
                        if st.button(reauth_label, key=f"btn_reauth_{acc['account_id']}", help="Reconnect this Google account with centralized OAuth"):
                            auth_url = get_oauth_auth_url(target_account_id=acc['account_id'])
                            if auth_url:
                                st.session_state.auth_url = auth_url
                                st.session_state.reauth_target_name = acc.get("channel_title") or acc["account_id"]
                                st.rerun()
                    with col_act3:
                        if st.button("❌ Disconnect", key=f"btn_disc_{acc['account_id']}", help="Revoke and remove this account"):
                            disconnect_account_api(acc["account_id"])
                            st.session_state.message = ("info", f"Disconnected '{acc['channel_title']}'.")
                            st.rerun()

                st.markdown("<hr style='margin: 8px 0; border: 0.5px solid #2b313e;'>", unsafe_allow_html=True)


# =============================================================================
# TAB 3: PDF COMMENT MANAGEMENT (NEW)
# =============================================================================
with tab3:
    st.subheader("PDF Comment Upload & Draft Management")
    st.write(
        "Upload a PDF containing prepared comments. Comments will be automatically extracted, parsed, "
        "and deduplicated while preserving original wording verbatim. You can preview, edit, or delete drafts."
    )

    pdf_file = st.file_uploader(
        "Upload PDF Document",
        type=["pdf"],
        help="Supports numbered lists, bullet points, paragraphs, and multiline comments.",
        key="pdf_uploader",
    )

    if pdf_file is not None:
        if st.button("📥 Extract Comments from PDF", type="primary", key="btn_extract_pdf"):
            try:
                with st.spinner("Extracting and parsing comments from PDF..."):
                    pdf_bytes = pdf_file.read()
                    data = pdf_extractor.extract_comments_from_pdf(pdf_bytes, remove_duplicates=True)
                    data["filename"] = pdf_file.name
                    st.session_state.pdf_data = data
                    st.session_state.draft_comments = list(data.get("comments", []))
                    st.session_state.message = (
                        "success",
                        f"Extracted {data.get('total_extracted')} comments ({data.get('total_unique')} unique, "
                        f"{data.get('duplicates_removed')} duplicates removed).",
                    )
                    st.rerun()
            except Exception as e:
                st.error(f"PDF Extraction failed: {str(e)}")

    if st.session_state.pdf_data and st.session_state.draft_comments:
        pdata = st.session_state.pdf_data
        st.write("---")

        # Extraction Metrics
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        with col_m1:
            st.metric("Source PDF", pdata.get("filename", "document.pdf")[:20])
        with col_m2:
            st.metric("Total Extracted", pdata.get("total_extracted", 0))
        with col_m3:
            st.metric("Unique Comments", len(st.session_state.draft_comments))
        with col_m4:
            st.metric("Duplicates Removed", pdata.get("duplicates_removed", 0))

        # Draft Comments Table & Editor
        st.markdown("### Draft Comments Preview & Management")
        search_draft = st.text_input("Filter draft comments", placeholder="Search keyword...", key="draft_search")

        filtered_drafts = [
            (idx, c) for idx, c in enumerate(st.session_state.draft_comments)
            if not search_draft or search_draft.lower() in c.lower()
        ]

        st.caption(f"Showing {len(filtered_drafts)} of {len(st.session_state.draft_comments)} comments")

        # Action: Clear All Comments
        col_clr, col_export = st.columns([1, 4])
        with col_clr:
            if st.button("🗑️ Clear All Comments", type="secondary"):
                st.session_state.draft_comments = []
                st.session_state.pdf_data = None
                st.session_state.assignments_list = []
                st.rerun()

        # Display editable comments list
        for original_idx, comment_text in filtered_drafts[:50]:
            with st.expander(f"Comment #{original_idx + 1} ({len(comment_text)} chars): {comment_text[:60]}..."):
                edit_val = st.text_area("Edit Text", value=comment_text, key=f"draft_txt_{original_idx}")
                col_save, col_del = st.columns([1, 5])
                with col_save:
                    if st.button("💾 Save", key=f"btn_save_draft_{original_idx}"):
                        st.session_state.draft_comments[original_idx] = edit_val.strip()
                        st.session_state.message = ("success", f"Saved Comment #{original_idx + 1}")
                        st.rerun()
                with col_del:
                    if st.button("❌ Remove", key=f"btn_del_draft_{original_idx}"):
                        st.session_state.draft_comments.pop(original_idx)
                        st.session_state.message = ("info", f"Removed Comment #{original_idx + 1}")
                        st.rerun()

        if len(filtered_drafts) > 50:
            st.info(f"Showing first 50 of {len(filtered_drafts)} comments. Use search filter to find specific comments.")
    elif not st.session_state.draft_comments:
        st.info("Upload a PDF file above to extract pre-written comments.")


# =============================================================================
# TAB 4: COMMENT REVIEW AND PUBLISHING (NEW)
# =============================================================================
with tab4:
    st.subheader("Comment Review and Account-Specific Publishing")
    st.write(
        "Assign extracted PDF comments to target videos and authorized YouTube accounts. "
        "Review every assignment, edit drafts, and explicitly approve before publishing."
    )

    if not connected_accounts:
        st.warning("⚠️ No connected YouTube accounts. Please connect at least one account in **Tab 2: Multi-Account Management**.")
    elif not st.session_state.draft_comments:
        st.warning("⚠️ No comments loaded. Please upload a PDF and extract comments in **Tab 3: PDF Comment Management**.")
    else:
        # Step 1: Input Target Videos
        st.markdown("#### Step 1: Specify Target YouTube Videos")
        vid_input_mode = st.radio("Target Video Input Source", ["Enter Video URLs", "Fetch Videos from Channel URL/@handle"], horizontal=True)

        target_videos: List[Dict[str, Any]] = []

        if vid_input_mode == "Enter Video URLs":
            raw_urls = st.text_area(
                "YouTube Video URLs (one URL or ID per line)",
                placeholder="https://www.youtube.com/watch?v=VIDEO_1\nhttps://youtu.be/VIDEO_2\nhttps://www.youtube.com/shorts/VIDEO_3",
                height=100,
                key="multi_video_urls",
            )
            if st.button("Resolve Video URLs", key="btn_resolve_urls"):
                resolved = []
                lines = [l.strip() for l in raw_urls.split("\n") if l.strip()]
                with st.spinner("Resolving video information..."):
                    for u in lines:
                        try:
                            vid = youtube_service.extract_video_id(u)
                            vinfo = asyncio.run(youtube_service.get_video_details(vid))
                            resolved.append(vinfo or {"video_id": vid, "title": f"Video {vid}", "url": f"https://www.youtube.com/watch?v={vid}"})
                        except Exception as e:
                            st.warning(f"Could not parse '{u}': {str(e)}")
                    st.session_state.target_videos = resolved
                    st.session_state.message = ("success", f"Resolved {len(resolved)} target videos.")
                    st.rerun()

        else:
            col_ch_in, col_ch_btn = st.columns([4, 1])
            with col_ch_in:
                channel_url_in = st.text_input("YouTube Channel URL, @handle, or Channel ID", placeholder="https://www.youtube.com/@ChannelHandle", key="tab4_channel_in")
            with col_ch_btn:
                st.write("")
                st.write("")
                btn_fetch_ch = st.button("Fetch Videos", key="tab4_fetch_ch_btn")

            if btn_fetch_ch and channel_url_in:
                try:
                    with st.spinner("Fetching channel videos..."):
                        ch_details = asyncio.run(youtube_channel_service.get_channel_details(channel_url_in.strip()))
                        uploads_id = ch_details.get("uploads_playlist_id")
                        if not uploads_id:
                            st.error("Channel found but it has no uploads playlist.")
                        else:
                            videos = asyncio.run(youtube_channel_service.get_channel_videos(uploads_id, max_videos=100))
                            if not videos:
                                st.warning(f"Channel '{ch_details.get('title')}' was found but no public videos could be retrieved.")
                            else:
                                st.session_state.target_videos = videos
                                st.session_state.message = ("success", f"Retrieved {len(videos)} videos from channel '{ch_details.get('title')}'.")
                                st.rerun()
                except Exception as e:
                    st.error(f"Failed to fetch channel videos: {str(e)}")

        target_videos = st.session_state.target_videos

        if target_videos:
            st.success(f"🎯 **{len(target_videos)} Target Videos Selected**")
            with st.expander(f"View Selected Videos ({len(target_videos)})", expanded=False):
                for v in target_videos[:10]:
                    st.write(f"- **{v.get('title', 'Video')}** (`{v.get('video_id')}`)")

            st.write("---")

            # Step 2: Assignment Configuration
            st.markdown("#### Step 2: Configure Multi-Account Assignment")
            col_acc_sel, col_mode_sel = st.columns(2)

            with col_acc_sel:
                account_choices = {acc["account_id"]: acc["channel_title"] for acc in connected_accounts}
                selected_accounts_for_assign = st.multiselect(
                    "Select Accounts to Distribute Across",
                    options=list(account_choices.keys()),
                    default=list(account_choices.keys()),
                    format_func=lambda x: account_choices[x],
                    help="Comments will be distributed safely across selected accounts. No video receives repetitive comments.",
                )

            with col_mode_sel:
                assign_mode = st.selectbox("Assignment Order", ["sequential", "random"], key="tab4_assign_mode")
                dist_strategy = st.selectbox(
                    "Account Commenting Strategy",
                    options=["auto", "each_account_per_video", "round_robin"],
                    format_func=lambda x: {
                        "auto": "⚡ Auto (Each account comments on video)",
                        "each_account_per_video": "👥 Each selected account posts on each video",
                        "round_robin": "🔄 Distribute videos across accounts (1 comment/video)",
                    }[x],
                    key="tab4_dist_strat",
                    help="Auto/Each account ensures every selected YouTube account gets its own unique comment for target video(s).",
                )

            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                excl_prev_videos = st.checkbox("Exclude previously commented videos", value=True, key="tab4_excl_vids")
            with col_opt2:
                excl_used_comments = st.checkbox("Exclude already used comments", value=True, key="tab4_excl_cmts")

            if st.button("⚡ Generate Assignments Table", type="primary", key="tab4_btn_assign"):
                if not selected_accounts_for_assign:
                    st.error("Please select at least one account for assignment.")
                else:
                    try:
                        chosen_acc_objects = [a for a in connected_accounts if a["account_id"] in selected_accounts_for_assign]
                        res = comment_assignment_engine.create_multi_account_assignments(
                            accounts=chosen_acc_objects,
                            videos=target_videos,
                            comments=st.session_state.draft_comments,
                            mode=assign_mode,
                            distribution_strategy=dist_strategy,
                            exclude_previously_commented_videos=excl_prev_videos,
                            exclude_already_used_comments=excl_used_comments,
                        )
                        st.session_state.assignments_list = res.get("assignments", [])
                        st.session_state.message = ("success", f"Generated {len(st.session_state.assignments_list)} assignments across {len(chosen_acc_objects)} accounts!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Assignment generation failed: {str(e)}")

        # Step 3: Interactive Review and Human Approval Table
        if st.session_state.assignments_list:
            st.write("---")
            st.markdown("#### Step 3: Review, Edit & Explicit Human Approval")
            st.write("Review each assigned comment. You can edit the text, toggle approval, or exclude specific items.")

            col_app_all, col_rej_all, col_spc = st.columns([1, 1, 3])
            with col_app_all:
                if st.button("✅ Approve All", key="btn_app_all"):
                    for i, item in enumerate(st.session_state.assignments_list):
                        if item.get("status") in ("success", "failed"):
                            continue
                        item["is_approved"] = True
                        item["status"] = "approved"
                        st.session_state[f"chk_app_{i}"] = True
                    st.rerun()
            with col_rej_all:
                if st.button("❌ Unapprove All", key="btn_rej_all"):
                    for i, item in enumerate(st.session_state.assignments_list):
                        if item.get("status") in ("success", "failed"):
                            continue
                        item["is_approved"] = False
                        item["status"] = "pending"
                        st.session_state[f"chk_app_{i}"] = False
                    st.rerun()

            # Render Table Rows
            for idx, item in enumerate(st.session_state.assignments_list):
                with st.container():
                    c_chk, c_acc, c_vid, c_txt, c_stat = st.columns([1, 2, 3, 4, 1])

                    with c_chk:
                        st.session_state.setdefault(f"chk_app_{idx}", bool(item.get("is_approved", False)))
                        is_app = st.checkbox("Approve", key=f"chk_app_{idx}")
                        item["is_approved"] = is_app
                        item["status"] = "approved" if is_app else "pending"

                    with c_acc:
                        st.markdown(f"**{item.get('account_name')}**")
                        st.caption(f"ID: `{item.get('channel_id')}`")

                    with c_vid:
                        st.markdown(f"[{item.get('video_title')}]({item.get('video_url')})")
                        st.caption(f"Video ID: `{item.get('video_id')}`")

                    with c_txt:
                        edit_cmt = st.text_area("Assigned Comment", value=item.get("approved_comment") or item.get("assigned_comment"), height=70, key=f"asgn_txt_{idx}")
                        item["approved_comment"] = edit_cmt.strip()

                    with c_stat:
                        if item.get("status") == "approved":
                            st.success("Approved")
                        elif item.get("status") == "success":
                            st.info("Published")
                        elif item.get("status") == "failed":
                            st.error("Failed")
                        else:
                            st.warning("Pending")

                    st.markdown("<hr style='margin: 4px 0;'>", unsafe_allow_html=True)

            # Step 4: Publish Approved Comments
            approved_assignments = [a for a in st.session_state.assignments_list if a.get("is_approved")]
            st.write("---")
            st.markdown("#### Step 4: Execute Publishing")
            st.write(f"**{len(approved_assignments)} of {len(st.session_state.assignments_list)} assignments approved for publishing.**")

            delay_sec = st.slider("Rate Limiting Delay Between Comments (seconds)", min_value=2, max_value=30, value=5, step=1, key="tab4_delay")
            confirm_pub = st.checkbox(
                "I have reviewed the assigned comments and explicitly authorize publishing to YouTube via official OAuth.",
                key="tab4_confirm_box",
            )

            if st.button("🚀 Publish Approved Comments Now", type="primary", disabled=not (approved_assignments and confirm_pub), key="tab4_btn_exec"):
                progress_bar = st.progress(0.0)
                status_text = st.empty()
                results_table = st.empty()

                pub_results = []
                failed_auth_accounts = set()

                for i, assign in enumerate(approved_assignments):
                    acc_id = assign.get("account_id")
                    v_url = assign.get("video_url")
                    c_text = assign.get("approved_comment") or assign.get("assigned_comment")

                    status_text.info(f"Publishing {i + 1}/{len(approved_assignments)} from '{assign.get('account_name')}' to '{assign.get('video_title')}'...")
                    try:
                        res = asyncio.run(
                            youtube_service.publish_comment_for_account(
                                account_id=acc_id,
                                video_url=v_url,
                                comment_text=c_text,
                                approved_comment=c_text,
                                pdf_filename=st.session_state.pdf_data.get("filename") if st.session_state.pdf_data else None,
                                pdf_hash=st.session_state.pdf_data.get("pdf_hash") if st.session_state.pdf_data else None,
                            )
                        )
                        assign["status"] = "success"
                        pub_results.append({
                            "Account": assign.get("account_name"),
                            "Video": assign.get("video_title"),
                            "Status": "🟢 Success",
                            "Comment ID / Error": res.get("comment_id", ""),
                        })
                    except Exception as e:
                        assign["status"] = "failed"
                        err_str = str(e)
                        if "401" in err_str or "reconnect" in err_str.lower() or "permission" in err_str.lower():
                            failed_auth_accounts.add(assign.get("account_name"))
                        pub_results.append({
                            "Account": assign.get("account_name"),
                            "Video": assign.get("video_title"),
                            "Status": "🔴 Failed",
                            "Comment ID / Error": err_str,
                        })

                    progress_bar.progress((i + 1) / len(approved_assignments))
                    if i < len(approved_assignments) - 1:
                        time.sleep(float(delay_sec))

                status_text.success("🎉 Publishing run completed!")
                results_table.dataframe(pd.DataFrame(pub_results), use_container_width=True)

                if failed_auth_accounts:
                    st.warning(
                        f"⚠️ Authentication error for: **{', '.join(failed_auth_accounts)}**. "
                        "Go to **Tab 2: Multi-Account Management** and click **🔄 Reconnect** to refresh their authorization."
                    )
                else:
                    st.session_state.message = ("success", "Publishing job completed successfully.")


# =============================================================================
# TAB 5: ACCOUNT-WISE HISTORY (NEW)
# =============================================================================
with tab5:
    st.subheader("Account-Wise Publishing History & Audit Logs")
    st.write("Inspect persistent publishing history stored in SQLite. Filter by account, status, source, or search keyword.")

    # Filter Controls
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)

    with f_col1:
        acc_filter_opts = ["All Accounts"] + [acc["channel_title"] for acc in connected_accounts]
        sel_acc_filter = st.selectbox("Filter by Account", acc_filter_opts, key="hist_acc_filter")
        target_acc_id = None
        if sel_acc_filter != "All Accounts":
            for a in connected_accounts:
                if a["channel_title"] == sel_acc_filter:
                    target_acc_id = a["account_id"]
                    break

    with f_col2:
        status_filter = st.selectbox("Publishing Status", ["All", "Success", "Failed"], key="hist_stat_filter")

    with f_col3:
        source_filter = st.selectbox("Comment Source", ["All", "PDF", "AI"], key="hist_src_filter")

    with f_col4:
        limit_val = st.selectbox("Rows Limit", [25, 50, 100, 250], index=1, key="hist_limit")

    search_term = st.text_input("Search (Video title, Video ID, Comment text, or Account name)", placeholder="Type to filter...", key="hist_search")

    # Fetch History Records
    history_records = db.get_history(
        limit=limit_val,
        status=status_filter if status_filter != "All" else None,
        source=source_filter if source_filter != "All" else None,
        search=search_term if search_term.strip() else None,
        account_id=target_acc_id,
    )

    if not history_records:
        st.info("No publishing history records found matching your filters.")
    else:
        st.write(f"**Found {len(history_records)} records:**")

        # Convert to DataFrame for presentation
        df_rows = []
        for r in history_records:
            df_rows.append({
                "Timestamp": str(r.get("publication_timestamp", ""))[:19],
                "Account": r.get("account_name") or r.get("account_id") or "Single Account",
                "Video Title": r.get("video_title") or r.get("video_id"),
                "Video ID": r.get("video_id"),
                "Published Comment": r.get("approved_comment") or r.get("assigned_comment"),
                "Source": str(r.get("comment_source", "")).upper(),
                "Status": "🟢 Success" if r.get("publishing_status") == "success" else "🔴 Failed",
                "Comment ID": r.get("published_comment_id") or "N/A",
                "Error": r.get("error_details") or "-",
            })

        df_display = pd.DataFrame(df_rows)
        st.dataframe(df_display, use_container_width=True, height=400)

        # Download CSV Export
        csv_buffer = io.StringIO()
        df_display.to_csv(csv_buffer, index=False)
        st.download_button(
            label="📥 Export History to CSV",
            data=csv_buffer.getvalue(),
            file_name=f"youtube_comment_history_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )


# =============================================================================
# TAB 6: SETTINGS & DIAGNOSTICS (UPGRADED)
# =============================================================================
with tab6:
    st.subheader("Settings, Environment & Diagnostics")

    # Google OAuth Status
    st.markdown("#### Centralized Google OAuth Configuration")
    st.info("Reused across ALL connected Google accounts. Tokens are isolated and encrypted at rest.")
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    c_masked = f"{cid[:10]}...{cid[-8:]}" if len(cid) > 18 else (cid or "Not Configured")
    st.text_input("Centralized Google Client ID", value=c_masked, disabled=True)
    st.text_input("Redirect URI", value=os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback"), disabled=True)
    st.text_input("OAuth Scopes", value=os.getenv("GOOGLE_SCOPES", "https://www.googleapis.com/auth/youtube.force-ssl"), disabled=True)
    st.text_input("Token Storage Security", value="AES-128-CBC + HMAC-SHA256 (Fernet) Encrypted at Rest", disabled=True)

    st.write("---")

    # AI Provider Settings
    st.markdown("#### AI Provider Settings (Mode 1)")
    ai_prov = os.getenv("AI_PROVIDER", "gemini").lower()
    st.write(f"**Active Provider**: `{ai_prov.title()}`")

    c_k1, c_k2 = st.columns(2)
    with c_k1:
        gemini_k = os.getenv("GEMINI_API_KEY", "")
        st.text_input("Gemini API Key", value=f"{gemini_k[:6]}...{gemini_k[-4:]}" if len(gemini_k) > 10 else ("Present" if gemini_k else "Not Set"), disabled=True)
    with c_k2:
        openai_k = os.getenv("OPENAI_API_KEY", "")
        st.text_input("OpenAI API Key", value=f"{openai_k[:6]}...{openai_k[-4:]}" if len(openai_k) > 10 else ("Present" if openai_k else "Not Set"), disabled=True)

    st.write("---")

    # SQLite Database Info
    st.markdown("#### SQLite Database Diagnostics")
    st.write(f"**Database Path**: `{db.db_path}`")
    st.write(f"**Database Size**: `{os.path.getsize(db.db_path) / 1024:.2f} KB`" if os.path.exists(db.db_path) else "File not created yet.")
    st.json(db.get_stats())
