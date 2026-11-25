"""Comment Publishing and AI Generation Routes with Account-Specific Publishing."""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.ai_service import ai_service
from app.services.database import db
from app.services.oauth_service import oauth_service
from app.services.youtube_service import youtube_service

router = APIRouter(prefix="/comment", tags=["Comments"])


class PublishCommentRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Optional session ID for legacy mode")
    account_id: Optional[str] = Field(None, description="Connected YouTube Account ID (e.g. acc_UC...)")
    video_url: str = Field(..., description="YouTube video URL or Video ID")
    comment: str = Field(..., min_length=1, max_length=10000, description="Comment text to publish")
    approved_comment: Optional[str] = Field(None, description="User-approved final comment text")


class GenerateCommentRequest(BaseModel):
    session_id: Optional[str] = Field(None, description="Optional session ID")
    account_id: Optional[str] = Field(None, description="Optional account ID")
    video_url: Optional[str] = Field(None, description="Optional YouTube video URL for context")
    prompt: Optional[str] = Field(None, description="Optional instructions, desired tone, or topic")


@router.post("/publish", summary="Publish Comment to YouTube Video")
async def publish_comment(payload: PublishCommentRequest):
    """
    Publishes a top-level comment to the specified YouTube video.
    If account_id is provided, strictly uses that specific account's OAuth authorization.
    """
    if not payload.video_url or not payload.video_url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube Video URL is required.",
        )

    if not payload.comment or not payload.comment.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Comment text cannot be empty.",
        )

    target_account_id = payload.account_id or (payload.session_id if payload.session_id and payload.session_id.startswith("acc_") else None)

    try:
        if target_account_id:
            result = await youtube_service.publish_comment_for_account(
                account_id=target_account_id.strip(),
                video_url=payload.video_url.strip(),
                comment_text=payload.comment.strip(),
                approved_comment=payload.approved_comment,
            )
            return result
        else:
            session_to_use = payload.session_id.strip() if payload.session_id else "default"
            result = await youtube_service.publish_comment(
                session_id=session_to_use,
                video_url=payload.video_url.strip(),
                comment_text=payload.comment.strip(),
            )
            return result

    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while publishing comment: {str(e)}",
        )


@router.post("/generate", summary="Generate Comment Using AI")
async def generate_comment(payload: GenerateCommentRequest):
    """
    Generates a natural, engaging comment using the configured AI provider (Gemini, OpenAI, or Ollama).
    Optionally extracts video context if video URL is provided.
    """
    video_info = None

    if payload.video_url and payload.video_url.strip():
        try:
            video_id = youtube_service.extract_video_id(payload.video_url.strip())
            access_token = None
            if payload.account_id:
                try:
                    access_token = await oauth_service.get_valid_access_token_for_account(payload.account_id.strip())
                except Exception:
                    pass
            elif payload.session_id:
                try:
                    access_token = await oauth_service.get_valid_access_token(payload.session_id.strip())
                except Exception:
                    pass

            video_info = await youtube_service.get_video_details(video_id=video_id, access_token=access_token)
        except Exception:
            pass

    try:
        generated_text = await ai_service.generate_comment(
            prompt=payload.prompt,
            video_info=video_info,
        )
        return {
            "status": "success",
            "comment": generated_text,
            "provider": ai_service.provider,
            "video_context_included": bool(video_info),
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate comment: {str(e)}",
        )
