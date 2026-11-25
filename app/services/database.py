"""SQLite Database Manager for Comment History, Runs, Multi-Account Management, and Duplicate Prevention."""

import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.services.crypto_service import crypto_service

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "comment_agent.db")


class Database:
    """Thread-safe SQLite persistent storage for accounts, comments, runs, and settings."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Database, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: str = DB_PATH) -> None:
        if getattr(self, "_initialized", False):
            return
        self.db_path = db_path
        self._init_db()
        self._initialized = True

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes database tables and performs automatic schema migrations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Accounts Table (for Multi-Account YouTube OAuth Management)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    channel_id TEXT UNIQUE NOT NULL,
                    channel_title TEXT NOT NULL,
                    channel_thumbnail TEXT,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at REAL NOT NULL,
                    status TEXT DEFAULT 'active',
                    is_active INTEGER DEFAULT 0,
                    google_email TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_auth_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Runs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    channel_url TEXT,
                    pdf_filename TEXT,
                    pdf_hash TEXT,
                    total_videos INTEGER DEFAULT 0,
                    total_comments INTEGER DEFAULT 0,
                    published_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    skipped_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'in_progress'
                )
            """)

            # 3. Comment history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS comment_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    video_id TEXT NOT NULL,
                    video_title TEXT,
                    video_url TEXT,
                    assigned_comment TEXT NOT NULL,
                    approved_comment TEXT,
                    account_id TEXT,
                    channel_id TEXT,
                    comment_source TEXT DEFAULT 'pdf',
                    publishing_status TEXT NOT NULL,
                    published_comment_id TEXT,
                    publication_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    error_details TEXT,
                    pdf_filename TEXT,
                    pdf_hash TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs (run_id),
                    FOREIGN KEY (account_id) REFERENCES accounts (account_id)
                )
            """)

            # 4. Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # Perform schema migration on comment_history for existing databases
            cursor.execute("PRAGMA table_info(comment_history)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            if "account_id" not in existing_cols:
                cursor.execute("ALTER TABLE comment_history ADD COLUMN account_id TEXT")
            if "channel_id" not in existing_cols:
                cursor.execute("ALTER TABLE comment_history ADD COLUMN channel_id TEXT")
            if "approved_comment" not in existing_cols:
                cursor.execute("ALTER TABLE comment_history ADD COLUMN approved_comment TEXT")

            # Perform schema migration on accounts for existing databases
            cursor.execute("PRAGMA table_info(accounts)")
            acc_cols = {row["name"] for row in cursor.fetchall()}
            if "google_email" not in acc_cols:
                cursor.execute("ALTER TABLE accounts ADD COLUMN google_email TEXT")
            if "error_message" not in acc_cols:
                cursor.execute("ALTER TABLE accounts ADD COLUMN error_message TEXT")

            # Indices for quick lookups and duplicate prevention
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_accounts_channel ON accounts (channel_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_video ON comment_history (video_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON comment_history (publishing_status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_run ON comment_history (run_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_account ON comment_history (account_id)")

            self._migrate_tokens_to_encrypted(conn)
            conn.commit()

    def _migrate_tokens_to_encrypted(self, conn: sqlite3.Connection) -> None:
        """Encrypts any plain text tokens currently stored in the database."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT account_id, access_token, refresh_token FROM accounts")
            rows = cursor.fetchall()
            for r in rows:
                acc_id = r["account_id"]
                acc_tok = r["access_token"]
                ref_tok = r["refresh_token"]
                need_update = False
                enc_acc = acc_tok
                enc_ref = ref_tok
                if acc_tok and not crypto_service.is_encrypted(acc_tok):
                    enc_acc = crypto_service.encrypt(acc_tok)
                    need_update = True
                if ref_tok and not crypto_service.is_encrypted(ref_tok):
                    enc_ref = crypto_service.encrypt(ref_tok)
                    need_update = True
                if need_update:
                    cursor.execute(
                        "UPDATE accounts SET access_token = ?, refresh_token = ? WHERE account_id = ?",
                        (enc_acc, enc_ref, acc_id),
                    )
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # MULTI-ACCOUNT MANAGEMENT METHODS
    # -------------------------------------------------------------------------

    def save_account(
        self,
        account_id: str,
        channel_id: str,
        channel_title: str,
        channel_thumbnail: Optional[str],
        access_token: str,
        refresh_token: Optional[str],
        expires_at: float,
        status: str = "active",
        is_active: Optional[bool] = None,
        google_email: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Saves or updates a connected YouTube account with its OAuth credentials."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # If is_active is not specified, check if any accounts exist yet
            if is_active is None:
                cursor.execute("SELECT COUNT(*) as count FROM accounts WHERE is_active = 1")
                has_active = cursor.fetchone()["count"] > 0
                active_val = 0 if has_active else 1
            else:
                active_val = 1 if is_active else 0

            # If setting this account as active, demote others
            if active_val == 1:
                cursor.execute("UPDATE accounts SET is_active = 0 WHERE account_id != ?", (account_id,))

            # Encrypt sensitive tokens at rest using AES-128-CBC + HMAC-SHA256 (Fernet)
            enc_access = crypto_service.encrypt(access_token)
            enc_refresh = crypto_service.encrypt(refresh_token) if refresh_token else None

            cursor.execute(
                """
                INSERT INTO accounts (
                    account_id, channel_id, channel_title, channel_thumbnail,
                    access_token, refresh_token, expires_at, status, is_active,
                    google_email, error_message, updated_at, last_auth_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(account_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    channel_title = excluded.channel_title,
                    channel_thumbnail = COALESCE(excluded.channel_thumbnail, accounts.channel_thumbnail),
                    access_token = excluded.access_token,
                    refresh_token = COALESCE(excluded.refresh_token, accounts.refresh_token),
                    expires_at = excluded.expires_at,
                    status = excluded.status,
                    is_active = CASE WHEN ? = 1 THEN 1 ELSE accounts.is_active END,
                    google_email = COALESCE(excluded.google_email, accounts.google_email),
                    error_message = excluded.error_message,
                    updated_at = CURRENT_TIMESTAMP,
                    last_auth_at = CURRENT_TIMESTAMP
                """,
                (
                    account_id,
                    channel_id,
                    channel_title,
                    channel_thumbnail,
                    enc_access,
                    enc_refresh,
                    expires_at,
                    status,
                    active_val,
                    google_email,
                    error_message,
                    active_val,
                ),
            )
            conn.commit()

    def cleanup_placeholder_accounts(self) -> int:
        """Removes any invalid or placeholder accounts that lack real YouTube channels."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM accounts WHERE channel_id LIKE 'channel_%' OR channel_title = 'No YouTube Channel'")
            conn.commit()
            return cursor.rowcount

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves raw account data with decrypted tokens (for internal backend services only)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            d["access_token"] = crypto_service.decrypt(d.get("access_token"))
            d["refresh_token"] = crypto_service.decrypt(d.get("refresh_token"))
            return d

    def get_account_by_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves account data by channel ID with decrypted tokens."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE channel_id = ?", (channel_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            d["access_token"] = crypto_service.decrypt(d.get("access_token"))
            d["refresh_token"] = crypto_service.decrypt(d.get("refresh_token"))
            return d

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Returns all connected accounts with tokens stripped for public/UI security."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts ORDER BY is_active DESC, updated_at DESC")
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["access_token"] = ""
                d["refresh_token"] = ""
                result.append(d)
            return result

    def set_active_account(self, account_id: str) -> bool:
        """Switches the default active account."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT account_id FROM accounts WHERE account_id = ?", (account_id,))
            if not cursor.fetchone():
                return False

            cursor.execute("UPDATE accounts SET is_active = 0")
            cursor.execute("UPDATE accounts SET is_active = 1 WHERE account_id = ?", (account_id,))
            conn.commit()
            return True

    def get_active_account(self) -> Optional[Dict[str, Any]]:
        """Returns the currently active account, or the first available account (decrypted tokens)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM accounts WHERE is_active = 1 LIMIT 1")
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT * FROM accounts ORDER BY updated_at DESC LIMIT 1")
                row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            d["access_token"] = crypto_service.decrypt(d.get("access_token"))
            d["refresh_token"] = crypto_service.decrypt(d.get("refresh_token"))
            return d

    def update_account_tokens(
        self,
        account_id: str,
        access_token: str,
        expires_at: float,
        refresh_token: Optional[str] = None,
    ) -> None:
        """Updates refreshed tokens for an account, encrypted at rest."""
        enc_access = crypto_service.encrypt(access_token)
        enc_refresh = crypto_service.encrypt(refresh_token) if refresh_token else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if enc_refresh:
                cursor.execute(
                    """
                    UPDATE accounts
                    SET access_token = ?, expires_at = ?, refresh_token = ?, status = 'active', error_message = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE account_id = ?
                    """,
                    (enc_access, expires_at, enc_refresh, account_id),
                )
            else:
                cursor.execute(
                    """
                    UPDATE accounts
                    SET access_token = ?, expires_at = ?, status = 'active', error_message = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE account_id = ?
                    """,
                    (enc_access, expires_at, account_id),
                )
            conn.commit()

    def update_account_status(self, account_id: str, status: str, error_message: Optional[str] = None) -> None:
        """Updates account status (e.g., 'active', 'reauth_required', 'disconnected') and optional error details."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if error_message is not None:
                cursor.execute(
                    "UPDATE accounts SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
                    (status, error_message, account_id),
                )
            else:
                cursor.execute(
                    "UPDATE accounts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
                    (status, account_id),
                )
            conn.commit()

    def delete_account(self, account_id: str) -> bool:
        """Removes an account from the database. Sets next available account as active if active was deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT is_active FROM accounts WHERE account_id = ?", (account_id,))
            row = cursor.fetchone()
            if not row:
                return False

            was_active = bool(row["is_active"])
            cursor.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))

            if was_active:
                cursor.execute("SELECT account_id FROM accounts ORDER BY updated_at DESC LIMIT 1")
                next_acc = cursor.fetchone()
                if next_acc:
                    cursor.execute("UPDATE accounts SET is_active = 1 WHERE account_id = ?", (next_acc["account_id"],))

            conn.commit()
            return True

    # -------------------------------------------------------------------------
    # RUNS AND COMMENT HISTORY METHODS
    # -------------------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        mode: str,
        channel_url: Optional[str] = None,
        pdf_filename: Optional[str] = None,
        pdf_hash: Optional[str] = None,
        total_videos: int = 0,
        total_comments: int = 0,
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO runs (run_id, mode, channel_url, pdf_filename, pdf_hash, total_videos, total_comments, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress')
                """,
                (run_id, mode, channel_url, pdf_filename, pdf_hash, total_videos, total_comments),
            )
            conn.commit()

    def update_run_status(
        self,
        run_id: str,
        status: str,
        published_count: int,
        failed_count: int,
        skipped_count: int,
    ) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE runs
                SET status = ?, published_count = ?, failed_count = ?, skipped_count = ?
                WHERE run_id = ?
                """,
                (status, published_count, failed_count, skipped_count, run_id),
            )
            conn.commit()

    def log_comment(
        self,
        video_id: str,
        video_title: str,
        video_url: str,
        assigned_comment: str,
        publishing_status: str,
        comment_source: str = "pdf",
        run_id: Optional[str] = None,
        account_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        approved_comment: Optional[str] = None,
        published_comment_id: Optional[str] = None,
        error_details: Optional[str] = None,
        pdf_filename: Optional[str] = None,
        pdf_hash: Optional[str] = None,
    ) -> int:
        """Logs publishing attempt with explicit account association and approved text."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO comment_history (
                    run_id, video_id, video_title, video_url, assigned_comment, approved_comment,
                    account_id, channel_id, comment_source, publishing_status, published_comment_id,
                    error_details, pdf_filename, pdf_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    video_id,
                    video_title,
                    video_url,
                    assigned_comment,
                    approved_comment or assigned_comment,
                    account_id,
                    channel_id,
                    comment_source,
                    publishing_status,
                    published_comment_id,
                    error_details,
                    pdf_filename,
                    pdf_hash,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_commented_video_ids(self) -> Set[str]:
        """Returns set of all video IDs where comments were successfully published."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT video_id FROM comment_history WHERE publishing_status = 'success'")
            rows = cursor.fetchall()
            return {row["video_id"] for row in rows}

    def get_used_comments(self) -> Set[str]:
        """Returns set of all comments that have already been successfully published."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT assigned_comment FROM comment_history WHERE publishing_status = 'success'")
            rows = cursor.fetchall()
            return {row["assigned_comment"].strip() for row in rows}

    def get_history(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        run_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieves publishing history with optional filtering by account, status, source, or search keyword."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT h.*, a.channel_title as account_name
                FROM comment_history h
                LEFT JOIN accounts a ON h.account_id = a.account_id
                WHERE 1=1
            """
            params: List[Any] = []

            if status and status.lower() != "all":
                query += " AND h.publishing_status = ?"
                params.append(status.lower())

            if source and source.lower() != "all":
                query += " AND h.comment_source = ?"
                params.append(source.lower())

            if run_id:
                query += " AND h.run_id = ?"
                params.append(run_id)

            if account_id and account_id.lower() != "all":
                query += " AND h.account_id = ?"
                params.append(account_id)

            if search and search.strip():
                query += " AND (h.video_title LIKE ? OR h.assigned_comment LIKE ? OR h.video_id LIKE ? OR a.channel_title LIKE ?)"
                term = f"%{search.strip()}%"
                params.extend([term, term, term, term])

            query += " ORDER BY h.publication_timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Calculates global dashboard statistics across accounts, runs, and comments."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_attempts,
                    SUM(CASE WHEN publishing_status = 'success' THEN 1 ELSE 0 END) as total_success,
                    SUM(CASE WHEN publishing_status = 'failed' THEN 1 ELSE 0 END) as total_failed,
                    SUM(CASE WHEN publishing_status = 'skipped' THEN 1 ELSE 0 END) as total_skipped
                FROM comment_history
            """)
            stats = dict(cursor.fetchone())

            cursor.execute("SELECT COUNT(*) as total_runs FROM runs")
            runs_stat = cursor.fetchone()
            stats["total_runs"] = runs_stat["total_runs"] if runs_stat else 0
            stats["total_success"] = stats["total_success"] or 0
            stats["total_failed"] = stats["total_failed"] or 0
            stats["total_skipped"] = stats["total_skipped"] or 0

            # Accounts statistics
            cursor.execute("SELECT COUNT(*) as total_accounts FROM accounts")
            stats["total_accounts"] = cursor.fetchone()["total_accounts"]

            cursor.execute("SELECT COUNT(*) as active_accounts FROM accounts WHERE status = 'active'")
            stats["active_accounts"] = cursor.fetchone()["active_accounts"]

            cursor.execute("SELECT COUNT(*) as reauth_accounts FROM accounts WHERE status = 'reauth_required'")
            stats["reauth_accounts"] = cursor.fetchone()["reauth_accounts"]

            return stats

    def get_setting(self, key: str, default: str = "") -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            conn.commit()


# Singleton database instance
db = Database()
