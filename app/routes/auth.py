"""Authentication and Google OAuth Routes with Multi-Account Support."""

import os
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.services.oauth_service import oauth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LogoutRequest(BaseModel):
    session_id: str = Field(..., description="Unique user session identifier")


class AccountActionRequest(BaseModel):
    account_id: str = Field(..., description="Target account ID (e.g. acc_UC...)")


@router.get("/youtube", summary="Get Google OAuth Authorization URL")
def get_auth_url(
    session_id: str = Query(..., description="Session identifier for state binding"),
    target_account_id: Optional[str] = Query(None, description="Optional account ID being reauthorized"),
    prompt: Optional[str] = Query("select_account consent", description="Google OAuth prompt parameter"),
):
    """
    Generates the Google OAuth 2.0 authorization URL for connecting or reauthorizing a YouTube account.
    Forces prompt='select_account consent' so Google always displays the account chooser.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="session_id parameter is required.",
        )

    try:
        auth_url = oauth_service.get_authorization_url(
            session_id=session_id.strip(),
            target_account_id=target_account_id.strip() if target_account_id else None,
            prompt=prompt,
        )
        return {
            "auth_url": auth_url,
            "session_id": session_id.strip(),
            "target_account_id": target_account_id,
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/callback", summary="Google OAuth Redirect Callback")
async def oauth_callback(
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """Handles the OAuth redirection from Google, exchanging the code for tokens and linking the account."""
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8501").rstrip("/")

    if error:
        err_msg = error_description or error
        html_error = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authorization Failed</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #0e1117; color: #ffffff; text-align: center; padding: 60px 20px; }}
                .card {{ max-width: 480px; margin: 0 auto; background: #1a1c24; border-radius: 12px; padding: 40px; border: 1px solid #ff4b4b; }}
                h2 {{ color: #ff4b4b; }}
                p {{ color: #a3a8b8; font-size: 16px; line-height: 1.5; }}
                a.btn {{ display: inline-block; margin-top: 24px; padding: 12px 24px; background: #ff4b4b; color: #fff; text-decoration: none; border-radius: 6px; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Authentication Cancelled or Failed</h2>
                <p>Google OAuth returned an error: <strong>{err_msg}</strong></p>
                <a href="{frontend_url}" class="btn">Return to YouTube AI Comment Agent</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_error, status_code=status.HTTP_400_BAD_REQUEST)

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state in OAuth callback parameters.",
        )

    try:
        result = await oauth_service.exchange_code(code=code, state=state)
        has_channel = result.get("has_channel", False)
        channel_title = result.get("channel_title") or "Authenticated User"
        channel_id = result.get("channel_id", "")
        account_id = result.get("account_id", "")
        google_email = result.get("google_email", "")
        message = result.get("message", "")

        if has_channel:
            card_content = f"""
                <h2 style="color: #28a745;">YouTube Channel Linked Successfully!</h2>
                <p>Your YouTube channel has been authorized and securely connected:</p>
                <div class="channel-name">{channel_title}</div>
                <p style="font-size: 13px; color: #8a8f9d;">Channel ID: <code>{channel_id}</code></p>
                {f'<p style="font-size: 13px; color: #8a8f9d;">Google Account: <strong>{google_email}</strong></p>' if google_email else ''}
                <p style="margin-top: 20px;">You can close this window and return to the dashboard:</p>
                <a href="{frontend_url}" class="btn" style="background: #28a745;">Return to YouTube AI Comment Agent</a>
                <script>
                    try {{
                        if (window.opener) {{
                            window.opener.location.reload();
                        }}
                    }} catch (e) {{}}
                </script>
            """
            border_color = "#28a745"
        else:
            card_content = f"""
                <h2 style="color: #f1c40f;">Google Account Connected — No Channel Found</h2>
                {f'<p>Authenticated with Google as: <strong>{google_email}</strong></p>' if google_email else ''}
                <p style="color: #e0e0e0; font-size: 15px; margin: 16px 0;">{message}</p>
                <div style="margin: 24px 0;">
                    <a href="https://www.youtube.com/create_channel" target="_blank" class="btn" style="background: #ff0000; margin-right: 10px;">1. Create YouTube Channel &rarr;</a>
                    <a href="{frontend_url}" class="btn" style="background: #383d4d;">Return to App</a>
                </div>
                <p style="font-size: 13px; color: #8a8f9d;">
                    After setting up your YouTube channel, return to the app and click <strong>Refresh Accounts</strong>.
                </p>
                <script>
                    try {{
                        if (window.opener) {{
                            window.opener.location.reload();
                        }}
                    }} catch (e) {{}}
                </script>
            """
            border_color = "#f1c40f"

        html_response = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>YouTube Account Connection</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #0e1117; color: #ffffff; text-align: center; padding: 60px 20px; }}
                .card {{ max-width: 540px; margin: 0 auto; background: #1a1c24; border-radius: 12px; padding: 40px; border: 1px solid {border_color}; }}
                p {{ color: #a3a8b8; font-size: 16px; line-height: 1.5; }}
                .channel-name {{ color: #ffffff; font-size: 20px; font-weight: 600; margin: 16px 0; background: #262936; padding: 10px 16px; border-radius: 6px; display: inline-block; }}
                a.btn {{ display: inline-block; margin-top: 10px; padding: 12px 24px; color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 600; transition: background 0.2s; }}
                a.btn:hover {{ opacity: 0.9; }}
            </style>
        </head>
        <body>
            <div class="card">
                {card_content}
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_response, status_code=status.HTTP_200_OK)

    except ValueError as e:
        html_error = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Authorization Error</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #0e1117; color: #ffffff; text-align: center; padding: 60px 20px; }}
                .card {{ max-width: 480px; margin: 0 auto; background: #1a1c24; border-radius: 12px; padding: 40px; border: 1px solid #ff4b4b; }}
                h2 {{ color: #ff4b4b; }}
                p {{ color: #a3a8b8; font-size: 16px; line-height: 1.5; }}
                a.btn {{ display: inline-block; margin-top: 24px; padding: 12px 24px; background: #ff4b4b; color: #fff; text-decoration: none; border-radius: 6px; font-weight: 600; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Authentication Error</h2>
                <p>{str(e)}</p>
                <a href="{frontend_url}" class="btn">Return to Application</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_error, status_code=status.HTTP_400_BAD_REQUEST)


# -----------------------------------------------------------------------------
# MULTI-ACCOUNT REST API ENDPOINTS
# -----------------------------------------------------------------------------

@router.get("/accounts", summary="List All Connected YouTube Accounts")
def list_connected_accounts():
    """
    Returns public metadata for all connected accounts.
    Never exposes OAuth access or refresh tokens.
    """
    accounts = oauth_service.list_accounts()
    active_account = oauth_service.get_active_account()
    return {
        "accounts": accounts,
        "total": len(accounts),
        "active_account_id": active_account["account_id"] if active_account else None,
    }


@router.post("/accounts/activate", summary="Set Default Active Account")
def set_active_account(payload: AccountActionRequest):
    """Sets the designated account as active for single-account operations."""
    success = oauth_service.set_active_account(payload.account_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{payload.account_id}' not found.",
        )
    return {"status": "success", "active_account_id": payload.account_id}


@router.post("/accounts/disconnect", summary="Disconnect and Revoke Account")
async def disconnect_account(payload: AccountActionRequest):
    """Revokes tokens with Google and removes account from database."""
    success = await oauth_service.disconnect_account(payload.account_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{payload.account_id}' not found.",
        )
    return {"status": "success", "message": f"Account '{payload.account_id}' disconnected."}


@router.get("/accounts/{account_id}/status", summary="Check Status of Specific Account")
def get_account_status(account_id: str):
    """Returns connection and token status for a specific connected account."""
    acc = oauth_service.get_account(account_id)
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{account_id}' not found.",
        )
    return acc


@router.post("/accounts/{account_id}/verify-channel", summary="Verify YouTube Channel for Account")
async def verify_account_channel(account_id: str):
    """Re-queries YouTube Data API for the account to check if a channel has been created."""
    try:
        result = await oauth_service.verify_account_channel(account_id.strip())
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# -----------------------------------------------------------------------------
# SESSION STATUS & LOGOUT (PRESERVED FOR BACKWARD COMPATIBILITY)
# -----------------------------------------------------------------------------

@router.get("/status", summary="Check Session / Active Account Status")
async def get_session_status(session_id: str = Query(..., description="Unique user session identifier")):
    """Checks whether the session or any active account is authenticated."""
    return await oauth_service.get_status(session_id.strip())


@router.post("/logout", summary="Log Out Session")
async def logout(payload: LogoutRequest):
    """Disconnects the current session."""
    logged_out = await oauth_service.logout(payload.session_id.strip())
    return {"logged_out": logged_out, "message": "Successfully logged out."}
