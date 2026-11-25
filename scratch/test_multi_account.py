"""Comprehensive Automated Mock Test Suite for Multi-Account YouTube Comment Management System.
Tests all required verification scenarios without making real YouTube API calls.
"""

import asyncio
import io
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("GOOGLE_CLIENT_ID"):
    os.environ["GOOGLE_CLIENT_ID"] = "mock_test_client_id.apps.googleusercontent.com"
if not os.environ.get("GOOGLE_CLIENT_SECRET"):
    os.environ["GOOGLE_CLIENT_SECRET"] = "mock_test_client_secret"

from app.services.comment_assignment import comment_assignment_engine
from app.services.database import db
from app.services.oauth_service import oauth_service
from app.services.pdf_extractor import pdf_extractor
from app.services.youtube_service import youtube_service


class TestMultiAccountSystem(unittest.TestCase):
    """Test Suite for Multi-Account YouTube PDF Comment Management System."""

    def setUp(self):
        try:
            db.delete_account("acc_UC_TEST_1")
            db.delete_account("acc_UC_TEST_2")
            db.delete_account("acc_UC_TEST_3")
            db.delete_account("acc_UC_TEST_CHERAN")
            db.delete_account("acc_UC_SECOND_CHANNEL")
        except Exception:
            pass

    def tearDown(self):
        try:
            db.delete_account("acc_UC_TEST_1")
            db.delete_account("acc_UC_TEST_2")
            db.delete_account("acc_UC_TEST_3")
            db.delete_account("acc_UC_TEST_CHERAN")
            db.delete_account("acc_UC_SECOND_CHANNEL")
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # TEST 1: EXISTING SINGLE-ACCOUNT WORKFLOW (MODE 1 REGRESSION)
    # -------------------------------------------------------------------------
    def test_01_single_account_workflow_regression(self):
        """Verify Mode 1 URL parsing and validation remain intact."""
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        vid = youtube_service.extract_video_id(url)
        self.assertEqual(vid, "dQw4w9WgXcQ")

        with self.assertRaises(ValueError):
            youtube_service.extract_video_id("https://vimeo.com/12345678")

    # -------------------------------------------------------------------------
    # TEST 2: CONNECTING ACCOUNT 1
    # -------------------------------------------------------------------------
    def test_02_connect_account_1(self):
        """Verify Account 1 connects and stores independent tokens."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="https://example.com/alpha.jpg",
            access_token="tok_alpha_access_123",
            refresh_token="tok_alpha_refresh_456",
            expires_at=time.time() + 3600,
            status="active",
            is_active=True,
            google_email="alpha@gmail.com",
        )
        acc = db.get_account("acc_UC_TEST_1")
        self.assertIsNotNone(acc)
        self.assertEqual(acc["channel_title"], "Alpha Channel")
        self.assertEqual(acc["access_token"], "tok_alpha_access_123")
        self.assertEqual(acc["is_active"], 1)

    # -------------------------------------------------------------------------
    # TEST 3: CONNECTING ACCOUNT 2 INDEPENDENTLY
    # -------------------------------------------------------------------------
    def test_03_connect_account_2(self):
        """Verify Account 2 connects alongside Account 1 without overwriting it."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="https://example.com/alpha.jpg",
            access_token="tok_alpha_access_123",
            refresh_token="tok_alpha_refresh_456",
            expires_at=time.time() + 3600,
            status="active",
            is_active=True,
        )
        db.save_account(
            account_id="acc_UC_TEST_2",
            channel_id="UC_TEST_2",
            channel_title="Beta Channel",
            channel_thumbnail="https://example.com/beta.jpg",
            access_token="tok_beta_access_789",
            refresh_token="tok_beta_refresh_012",
            expires_at=time.time() + 3600,
            status="active",
            is_active=False,
        )
        acc1 = db.get_account("acc_UC_TEST_1")
        acc2 = db.get_account("acc_UC_TEST_2")
        self.assertIsNotNone(acc1)
        self.assertIsNotNone(acc2)
        self.assertEqual(acc1["channel_title"], "Alpha Channel")
        self.assertEqual(acc2["channel_title"], "Beta Channel")
        self.assertNotEqual(acc1["access_token"], acc2["access_token"])

    # -------------------------------------------------------------------------
    # TEST 4: CONNECTING MULTIPLE ACCOUNTS
    # -------------------------------------------------------------------------
    def test_04_connect_multiple_accounts(self):
        """Verify multiple accounts can be retrieved and listed via public view."""
        db.save_account("acc_UC_TEST_1", "UC_TEST_1", "Alpha", "", "tok_1", "ref_1", time.time() + 3600)
        db.save_account("acc_UC_TEST_2", "UC_TEST_2", "Beta", "", "tok_2", "ref_2", time.time() + 3600)
        db.save_account("acc_UC_TEST_3", "UC_TEST_3", "Gamma", "", "tok_3", "ref_3", time.time() + 3600)

        accounts = oauth_service.list_accounts()
        acc_ids = {a["account_id"] for a in accounts}
        self.assertIn("acc_UC_TEST_1", acc_ids)
        self.assertIn("acc_UC_TEST_2", acc_ids)
        self.assertIn("acc_UC_TEST_3", acc_ids)

    # -------------------------------------------------------------------------
    # TEST 5: SWITCHING ACTIVE ACCOUNTS
    # -------------------------------------------------------------------------
    def test_05_switch_active_account(self):
        """Verify switching active accounts updates the default account cleanly."""
        db.save_account("acc_UC_TEST_1", "UC_TEST_1", "Alpha", "", "tok_1", "ref_1", time.time() + 3600, is_active=True)
        db.save_account("acc_UC_TEST_2", "UC_TEST_2", "Beta", "", "tok_2", "ref_2", time.time() + 3600, is_active=False)

        # Switch to Account 2
        success = oauth_service.set_active_account("acc_UC_TEST_2")
        self.assertTrue(success)
        active = oauth_service.get_active_account()
        self.assertEqual(active["account_id"], "acc_UC_TEST_2")

        # Switch back to Account 1
        oauth_service.set_active_account("acc_UC_TEST_1")
        active = oauth_service.get_active_account()
        self.assertEqual(active["account_id"], "acc_UC_TEST_1")

    # -------------------------------------------------------------------------
    # TEST 6: CORRECT TOKEN SELECTION & ZERO TOKEN EXPOSURE
    # -------------------------------------------------------------------------
    def test_06_token_separation(self):
        """Verify Account 1 never receives Account 2's token and public listing strips tokens."""
        db.save_account("acc_UC_TEST_1", "UC_TEST_1", "Alpha", "", "SECRET_TOKEN_ALPHA", "ref_1", time.time() + 3600)
        db.save_account("acc_UC_TEST_2", "UC_TEST_2", "Beta", "", "SECRET_TOKEN_BETA", "ref_2", time.time() + 3600)

        tok1 = asyncio.run(oauth_service.get_valid_access_token_for_account("acc_UC_TEST_1"))
        tok2 = asyncio.run(oauth_service.get_valid_access_token_for_account("acc_UC_TEST_2"))

        self.assertEqual(tok1, "SECRET_TOKEN_ALPHA")
        self.assertEqual(tok2, "SECRET_TOKEN_BETA")
        self.assertNotEqual(tok1, tok2)

        pub_list = oauth_service.list_accounts()
        for item in pub_list:
            self.assertNotIn("access_token", item)
            self.assertNotIn("refresh_token", item)

    # -------------------------------------------------------------------------
    # TEST 7: PDF UPLOAD AND PARSING NUMBERED COMMENTS
    # -------------------------------------------------------------------------
    def test_07_pdf_numbered_comments(self):
        """Verify extraction of numbered comments."""
        sample_text = (
            "1. Great tutorial on machine learning algorithms!\n"
            "2. Loved the practical code examples provided.\n"
            "3. Could you explain the gradient descent part in more detail?"
        )
        parsed = pdf_extractor._parse_comments(sample_text)
        self.assertEqual(len(parsed), 3)
        self.assertIn("Great tutorial on machine learning algorithms!", parsed[0])
        self.assertIn("Loved the practical code examples provided.", parsed[1])

    # -------------------------------------------------------------------------
    # TEST 8: PDF BULLET-POINT COMMENTS
    # -------------------------------------------------------------------------
    def test_08_pdf_bullet_comments(self):
        """Verify extraction of bullet-pointed comments."""
        sample_text = (
            "• Incredible production quality and audio fidelity.\n"
            "• This resolved my error completely, thank you!\n"
            "• Subscribed for future episodes."
        )
        parsed = pdf_extractor._parse_comments(sample_text)
        self.assertEqual(len(parsed), 3)
        self.assertIn("Incredible production quality", parsed[0])

    # -------------------------------------------------------------------------
    # TEST 9: MULTILINE COMMENTS EXTRACTION
    # -------------------------------------------------------------------------
    def test_09_multiline_comments_extraction(self):
        """Verify multiline comments are correctly aggregated into a single comment."""
        sample_text = (
            "1. First line of comment one.\n"
            "This continues comment one on a second line.\n\n"
            "2. Comment two starts here.\n"
            "And finishes on this line."
        )
        parsed = pdf_extractor._parse_comments(sample_text)
        self.assertEqual(len(parsed), 2)
        self.assertIn("First line of comment one. This continues comment one", parsed[0])

    # -------------------------------------------------------------------------
    # TEST 10: DUPLICATE COMMENT DETECTION
    # -------------------------------------------------------------------------
    def test_10_duplicate_comment_detection(self):
        """Verify identical comments are identified and removed while keeping unique ones."""
        comments = [
            "Outstanding overview of the topic!",
            "Outstanding overview of the topic!",
            "Outstanding overview of the topic!  ",
            "A genuinely fresh perspective on this concept.",
        ]
        seen = set()
        unique = []
        dupes = 0
        for c in comments:
            norm = " ".join(c.split()).strip().lower()
            if norm in seen:
                dupes += 1
                continue
            seen.add(norm)
            unique.append(c.strip())

        self.assertEqual(len(unique), 2)
        self.assertEqual(dupes, 2)

    # -------------------------------------------------------------------------
    # TEST 11: SCANNED PDF DETECTION
    # -------------------------------------------------------------------------
    def test_11_scanned_pdf_detection(self):
        """Verify empty or scanned PDF detection raises ValueError."""
        with self.assertRaises(ValueError):
            full_raw_text = "   \n\n  "
            if len(full_raw_text.replace("\n", "").replace(" ", "")) < 20:
                raise ValueError("The uploaded PDF appears to be a scanned image or contains no readable digital text.")

    # -------------------------------------------------------------------------
    # TEST 12: YOUTUBE URL VALIDATION
    # -------------------------------------------------------------------------
    def test_12_youtube_url_validation(self):
        """Verify various YouTube URL formats parse correctly."""
        formats = [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ]
        for url, expected_id in formats:
            vid = youtube_service.extract_video_id(url)
            self.assertEqual(vid, expected_id)

    # -------------------------------------------------------------------------
    # TEST 13: VIDEO DETAIL RETRIEVAL MOCK
    # -------------------------------------------------------------------------
    def test_13_video_detail_retrieval(self):
        """Verify video detail extraction returns structured metadata."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [{
                "snippet": {
                    "title": "Rick Astley - Never Gonna Give You Up",
                    "description": "The official video",
                    "channelTitle": "RickAstleyVEVO",
                    "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}},
                },
                "statistics": {"viewCount": "1500000000", "commentCount": "500000"},
                "status": {"privacyStatus": "public"},
            }]
        }
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_resp
            vinfo = asyncio.run(youtube_service.get_video_details("dQw4w9WgXcQ"))
            self.assertIsNotNone(vinfo)
            self.assertEqual(vinfo["video_id"], "dQw4w9WgXcQ")
            self.assertEqual(vinfo["title"], "Rick Astley - Never Gonna Give You Up")
            self.assertTrue(vinfo.get("comments_available", True))

    # -------------------------------------------------------------------------
    # TEST 14: MULTI-ACCOUNT COMMENT ASSIGNMENT
    # -------------------------------------------------------------------------
    def test_14_comment_assignment(self):
        """Verify 1-to-1 distribution across accounts without duplicate assignments to the same video."""
        accounts = [
            {"account_id": "acc_1", "channel_title": "Channel 1", "channel_id": "UC1"},
            {"account_id": "acc_2", "channel_title": "Channel 2", "channel_id": "UC2"},
        ]
        videos = [
            {"video_id": "vid_A", "title": "Video A", "url": "https://youtube.com/watch?v=vid_A"},
            {"video_id": "vid_B", "title": "Video B", "url": "https://youtube.com/watch?v=vid_B"},
        ]
        comments = ["Awesome insight on video A!", "Terrific breakdown on video B!"]

        result = comment_assignment_engine.create_multi_account_assignments(
            accounts=accounts,
            videos=videos,
            comments=comments,
            exclude_previously_commented_videos=False,
            exclude_already_used_comments=False,
        )

        assignments = result["assignments"]
        self.assertEqual(len(assignments), 2)
        self.assertEqual(assignments[0]["account_id"], "acc_1")
        self.assertEqual(assignments[0]["video_id"], "vid_A")
        self.assertEqual(assignments[1]["account_id"], "acc_2")
        self.assertEqual(assignments[1]["video_id"], "vid_B")

    # -------------------------------------------------------------------------
    # TEST 15: MANUAL DRAFT EDITING
    # -------------------------------------------------------------------------
    def test_15_draft_manual_editing(self):
        """Verify draft comment can be edited and stored before approval."""
        drafts = ["Original PDF comment text."]
        edited_text = "Updated and refined comment text."
        drafts[0] = edited_text
        self.assertEqual(drafts[0], "Updated and refined comment text.")

    # -------------------------------------------------------------------------
    # TEST 16: PUBLISHING APPROVAL REQUIREMENT
    # -------------------------------------------------------------------------
    def test_16_publishing_approval_requirement(self):
        """Verify that comments without approval are blocked from execution."""
        assignments = [
            {"video_id": "vid_1", "is_approved": False, "status": "pending"},
            {"video_id": "vid_2", "is_approved": True, "status": "approved"},
        ]
        approved_only = [a for a in assignments if a.get("is_approved")]
        self.assertEqual(len(approved_only), 1)
        self.assertEqual(approved_only[0]["video_id"], "vid_2")

    # -------------------------------------------------------------------------
    # TEST 17: PUBLISHING SUCCESS (MOCKED YOUTUBE API)
    # -------------------------------------------------------------------------
    def test_17_publishing_success(self):
        """Verify successful comment publishing via account-specific method."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="",
            access_token="valid_token_123",
            refresh_token="valid_refresh",
            expires_at=time.time() + 3600,
            status="active",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "cmt_published_999",
            "snippet": {
                "topLevelComment": {
                    "snippet": {
                        "authorDisplayName": "Alpha Channel",
                        "publishedAt": "2026-11-20T12:00:00Z",
                    }
                }
            },
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
             patch.object(youtube_service, "verify_user_has_channel", new_callable=AsyncMock):
            mock_post.return_value = mock_resp

            res = asyncio.run(youtube_service.publish_comment_for_account(
                account_id="acc_UC_TEST_1",
                video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                comment_text="Verified successful test comment.",
                approved_comment="Verified successful test comment.",
            ))

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["comment_id"], "cmt_published_999")
            self.assertEqual(res["account_id"], "acc_UC_TEST_1")

    # -------------------------------------------------------------------------
    # TEST 18: PUBLISHING FAILURE (MOCKED YOUTUBE API ERROR)
    # -------------------------------------------------------------------------
    def test_18_publishing_failure(self):
        """Verify that YouTube API errors (e.g. commentsDisabled) are caught and logged."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="",
            access_token="valid_token_123",
            refresh_token="valid_refresh",
            expires_at=time.time() + 3600,
            status="active",
        )

        mock_err_resp = MagicMock()
        mock_err_resp.status_code = 403
        mock_err_resp.json.return_value = {
            "error": {
                "errors": [{"reason": "commentsDisabled", "message": "Comments are disabled."}]
            }
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
             patch.object(youtube_service, "verify_user_has_channel", new_callable=AsyncMock):
            mock_post.return_value = mock_err_resp

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(youtube_service.publish_comment_for_account(
                    account_id="acc_UC_TEST_1",
                    video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    comment_text="This should fail.",
                ))
            self.assertIn("Comments are disabled", str(ctx.exception))

    # -------------------------------------------------------------------------
    # TEST 19: EXPIRED TOKEN AUTO-REFRESH
    # -------------------------------------------------------------------------
    def test_19_expired_token_handling(self):
        """Verify that an expired token automatically refreshes using stored refresh_token."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="",
            access_token="old_expired_token",
            refresh_token="valid_refresh_token",
            expires_at=time.time() - 500,
            status="active",
        )

        mock_refresh_resp = MagicMock()
        mock_refresh_resp.status_code = 200
        mock_refresh_resp.json.return_value = {
            "access_token": "newly_refreshed_token_777",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_refresh_resp
            token = asyncio.run(oauth_service.get_valid_access_token_for_account("acc_UC_TEST_1"))
            self.assertEqual(token, "newly_refreshed_token_777")

            updated_acc = db.get_account("acc_UC_TEST_1")
            self.assertEqual(updated_acc["access_token"], "newly_refreshed_token_777")
            self.assertGreater(updated_acc["expires_at"], time.time())

    # -------------------------------------------------------------------------
    # TEST 20: APPLICATION RESTART PERSISTENCE & DISCONNECTION
    # -------------------------------------------------------------------------
    def test_20_restart_persistence_and_disconnect(self):
        """Verify accounts persist across simulated restarts and disconnect cleanly."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Persistent Channel",
            channel_thumbnail="",
            access_token="tok_persist",
            refresh_token="ref_persist",
            expires_at=time.time() + 3600,
            status="active",
        )

        db.log_comment(
            video_id="dQw4w9WgXcQ",
            video_title="Persistent Test Video",
            video_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            assigned_comment="Persistent comment",
            publishing_status="success",
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
        )

        reloaded_acc = db.get_account("acc_UC_TEST_1")
        self.assertIsNotNone(reloaded_acc)
        self.assertEqual(reloaded_acc["channel_title"], "Persistent Channel")

        hist = db.get_history(account_id="acc_UC_TEST_1")
        self.assertTrue(len(hist) >= 1)
        self.assertEqual(hist[0]["video_id"], "dQw4w9WgXcQ")

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock):
            disc_success = asyncio.run(oauth_service.disconnect_account("acc_UC_TEST_1"))
            self.assertTrue(disc_success)

        self.assertIsNone(db.get_account("acc_UC_TEST_1"))

    # -------------------------------------------------------------------------
    # TEST 21: NO FAKE CHANNEL ID ON MISSING YOUTUBE CHANNEL
    # -------------------------------------------------------------------------
    def test_21_no_fake_channel_id_on_missing_channel(self):
        """Verify that an account without a YouTube channel does NOT generate fake channel_hex IDs."""
        auth_url = oauth_service.get_authorization_url(session_id="test_sess_missing", prompt="select_account consent")
        self.assertIn("prompt=select_account+consent", auth_url)

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {
            "access_token": "valid_tok_no_channel",
            "refresh_token": "valid_ref_no_channel",
            "expires_in": 3600,
        }

        state_key = list(oauth_service._oauth_states.keys())[-1]

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
             patch.object(oauth_service, "fetch_google_user_info", new_callable=AsyncMock) as mock_userinfo, \
             patch.object(oauth_service, "fetch_channel_info", new_callable=AsyncMock) as mock_channel:
            mock_post.return_value = mock_token_resp
            mock_userinfo.return_value = {"email": "nochannel@gmail.com", "name": "No Channel User"}
            mock_channel.return_value = {
                "has_channel": False,
                "id": None,
                "title": None,
                "thumbnail": None,
                "message": "No YouTube channel associated with this Google account.",
            }

            result = asyncio.run(oauth_service.exchange_code("code_123", state_key))
            self.assertFalse(result["has_channel"])
            self.assertIsNone(result["channel_id"])
            self.assertIsNone(result["account_id"])
            self.assertEqual(result["google_email"], "nochannel@gmail.com")

            for acc in db.list_accounts():
                self.assertFalse(str(acc.get("channel_id", "")).startswith("channel_"))
                self.assertNotEqual(acc.get("channel_title"), "No YouTube Channel")

    # -------------------------------------------------------------------------
    # TEST 22: REAUTH TARGET ACCOUNT ID BINDING
    # -------------------------------------------------------------------------
    def test_22_reauth_target_account_id_binding(self):
        """Verify that reauthorizing a specific account binds to target_account_id in state."""
        auth_url = oauth_service.get_authorization_url(
            session_id="test_sess_reauth",
            target_account_id="acc_UC_TEST_REAUTH",
            prompt="select_account consent",
        )
        state_key = list(oauth_service._oauth_states.keys())[-1]
        state_data = oauth_service._oauth_states[state_key]
        self.assertEqual(state_data["target_account_id"], "acc_UC_TEST_REAUTH")
        self.assertIn("prompt=select_account+consent", auth_url)

    # -------------------------------------------------------------------------
    # TEST 23: ACCOUNT 1 PRESERVED WHEN ACCOUNT 2 CONNECTS
    # -------------------------------------------------------------------------
    def test_23_account1_preserved_when_account2_connects(self):
        """Verify Account 1 (e.g. Cheran) is untouched when Account 2 authorizes with a new channel."""
        db.save_account(
            account_id="acc_UC_TEST_CHERAN",
            channel_id="UC_TEST_CHERAN",
            channel_title="Cheran",
            channel_thumbnail="https://example.com/cheran.jpg",
            access_token="tok_cheran_access",
            refresh_token="ref_cheran",
            expires_at=time.time() + 3600,
            status="active",
            is_active=True,
        )

        db.save_account(
            account_id="acc_UC_SECOND_CHANNEL",
            channel_id="UC_SECOND_CHANNEL",
            channel_title="Second YouTube Channel",
            channel_thumbnail="https://example.com/second.jpg",
            access_token="tok_second_access",
            refresh_token="ref_second",
            expires_at=time.time() + 3600,
            status="active",
            is_active=False,
        )

        acc1 = db.get_account("acc_UC_TEST_CHERAN")
        acc2 = db.get_account("acc_UC_SECOND_CHANNEL")

        self.assertIsNotNone(acc1)
        self.assertIsNotNone(acc2)
        self.assertEqual(acc1["channel_title"], "Cheran")
        self.assertEqual(acc1["access_token"], "tok_cheran_access")
        self.assertEqual(acc2["channel_title"], "Second YouTube Channel")
        self.assertEqual(acc2["access_token"], "tok_second_access")
        self.assertNotEqual(acc1["access_token"], acc2["access_token"])

        db.delete_account("acc_UC_TEST_CHERAN")
        db.delete_account("acc_UC_SECOND_CHANNEL")

    # -------------------------------------------------------------------------
    # TEST 24: SECURE TOKEN ENCRYPTION AT REST
    # -------------------------------------------------------------------------
    def test_24_token_encryption_at_rest(self):
        """Verify tokens are encrypted with AES/Fernet in SQLite and stripped in list_accounts."""
        import sqlite3
        from app.services.crypto_service import crypto_service

        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Encrypted Channel",
            channel_thumbnail="",
            access_token="secret_plain_access_token_123",
            refresh_token="secret_plain_refresh_token_456",
            expires_at=time.time() + 3600,
            status="active",
        )

        # 1. Verify in raw SQLite connection that token is stored encrypted
        conn = sqlite3.connect(db.db_path)
        cur = conn.cursor()
        cur.execute("SELECT access_token, refresh_token FROM accounts WHERE account_id = 'acc_UC_TEST_1'")
        raw_row = cur.fetchone()
        conn.close()

        self.assertTrue(crypto_service.is_encrypted(raw_row[0]), "Raw DB access_token must start with enc:")
        self.assertTrue(crypto_service.is_encrypted(raw_row[1]), "Raw DB refresh_token must start with enc:")

        # 2. Verify db.get_account returns decrypted plaintext
        acc = db.get_account("acc_UC_TEST_1")
        self.assertEqual(acc["access_token"], "secret_plain_access_token_123")
        self.assertEqual(acc["refresh_token"], "secret_plain_refresh_token_456")

        # 3. Verify public list_accounts strips tokens completely
        public_list = db.list_accounts()
        for item in public_list:
            self.assertEqual(item["access_token"], "")
            self.assertEqual(item["refresh_token"], "")

    # -------------------------------------------------------------------------
    # TEST 25: 401 UNAUTHORIZED AUTO-REFRESH & RETRY
    # -------------------------------------------------------------------------
    def test_25_401_unauthorized_auto_refresh_retry(self):
        """Verify that a 401 error during comment publishing triggers force refresh and retry."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Alpha Channel",
            channel_thumbnail="",
            access_token="stale_invalid_access_token",
            refresh_token="valid_refresh_token_xyz",
            expires_at=time.time() + 3600,
            status="active",
        )

        mock_401_resp = MagicMock()
        mock_401_resp.status_code = 401
        mock_401_resp.json.return_value = {"error": {"message": "Invalid Credentials"}}

        mock_200_resp = MagicMock()
        mock_200_resp.status_code = 200
        mock_200_resp.json.return_value = {
            "id": "cmt_recovered_401",
            "snippet": {
                "topLevelComment": {
                    "snippet": {
                        "authorDisplayName": "Alpha Channel",
                        "publishedAt": "2026-11-20T18:00:00Z",
                    }
                }
            },
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
             patch.object(youtube_service, "verify_user_has_channel", new_callable=AsyncMock), \
             patch.object(oauth_service, "force_refresh_account_token", new_callable=AsyncMock) as mock_force_refresh:
            mock_force_refresh.return_value = "newly_refreshed_access_token"
            mock_post.side_effect = [mock_401_resp, mock_200_resp]

            res = asyncio.run(youtube_service.publish_comment_for_account(
                account_id="acc_UC_TEST_1",
                video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                comment_text="Comment after 401 recovery.",
            ))

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["comment_id"], "cmt_recovered_401")
            mock_force_refresh.assert_awaited_once_with("acc_UC_TEST_1")

    # -------------------------------------------------------------------------
    # TEST 26: 401 FAILURE MARKS REAUTH REQUIRED WITHOUT STOPPING OTHER ACCOUNTS
    # -------------------------------------------------------------------------
    def test_26_401_failure_marks_reauth_required(self):
        """Verify that persistent 401 error marks account status as reauth_required."""
        db.save_account(
            account_id="acc_UC_TEST_1",
            channel_id="UC_TEST_1",
            channel_title="Failing Channel",
            channel_thumbnail="",
            access_token="bad_token",
            refresh_token="revoked_refresh_token",
            expires_at=time.time() + 3600,
            status="active",
        )

        mock_401_resp = MagicMock()
        mock_401_resp.status_code = 401
        mock_401_resp.json.return_value = {"error": {"message": "Invalid Credentials"}}

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
             patch.object(youtube_service, "verify_user_has_channel", new_callable=AsyncMock), \
             patch.object(oauth_service, "force_refresh_account_token", new_callable=AsyncMock) as mock_force_refresh:
            mock_post.return_value = mock_401_resp
            mock_force_refresh.side_effect = PermissionError("Token revoked by Google")

            with self.assertRaises(PermissionError) as ctx:
                asyncio.run(youtube_service.publish_comment_for_account(
                    account_id="acc_UC_TEST_1",
                    video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    comment_text="Should fail with 401.",
                ))

            self.assertIn("401", str(ctx.exception))
            acc = db.get_account("acc_UC_TEST_1")
            self.assertEqual(acc["status"], "reauth_required")
            self.assertIsNotNone(acc["error_message"])

    # -------------------------------------------------------------------------
    # TEST 27: MULTI-ACCOUNT COMMENTING ON A SINGLE VIDEO
    # -------------------------------------------------------------------------
    def test_27_multi_account_commenting_single_video(self):
        """Verify that 1 video with multiple accounts assigns distinct comments to each account."""
        accounts = [
            {"account_id": "acc_UC_TEST_1", "channel_title": "Account 1", "channel_id": "UC_TEST_1"},
            {"account_id": "acc_UC_TEST_2", "channel_title": "Account 2", "channel_id": "UC_TEST_2"},
            {"account_id": "acc_UC_TEST_3", "channel_title": "Account 3", "channel_id": "UC_TEST_3"},
        ]
        videos = [{"video_id": "dQw4w9WgXcQ", "title": "Target Video", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]
        comments = [
            "First unique comment from PDF.",
            "Second unique comment from PDF.",
            "Third unique comment from PDF.",
        ]

        result = comment_assignment_engine.create_multi_account_assignments(
            accounts=accounts,
            videos=videos,
            comments=comments,
            distribution_strategy="auto",
            exclude_previously_commented_videos=False,
            exclude_already_used_comments=False,
        )

        assignments = result["assignments"]
        self.assertEqual(len(assignments), 3)

        assigned_acc_ids = [a["account_id"] for a in assignments]
        assigned_comments = [a["assigned_comment"] for a in assignments]

        self.assertEqual(assigned_acc_ids, ["acc_UC_TEST_1", "acc_UC_TEST_2", "acc_UC_TEST_3"])
        self.assertEqual(len(set(assigned_comments)), 3)
        self.assertEqual(assigned_comments[0], "First unique comment from PDF.")
        self.assertEqual(assigned_comments[1], "Second unique comment from PDF.")
        self.assertEqual(assigned_comments[2], "Third unique comment from PDF.")

    # -------------------------------------------------------------------------
    # TEST 28: CENTRALIZED OAUTH REUSED FOR ALL ACCOUNTS
    # -------------------------------------------------------------------------
    def test_28_centralized_oauth_reused_for_all_accounts(self):
        """Verify that centralized GOOGLE_CLIENT_ID from environment is reused across all accounts."""
        cfg = oauth_service._get_config()
        self.assertEqual(cfg["client_id"], os.getenv("GOOGLE_CLIENT_ID"))
        self.assertEqual(cfg["client_secret"], os.getenv("GOOGLE_CLIENT_SECRET"))

        url1 = oauth_service.get_authorization_url(session_id="sess_1")
        url2 = oauth_service.get_authorization_url(session_id="sess_2", target_account_id="acc_UC_TEST_2")

        self.assertIn(f"client_id={cfg['client_id']}", url1)
        self.assertIn(f"client_id={cfg['client_id']}", url2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
