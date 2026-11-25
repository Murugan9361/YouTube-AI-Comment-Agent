"""FastAPI routes for YouTube Channel, PDF commenting, and Run History."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.comment_assignment import comment_assignment_engine
from app.services.comment_publisher import comment_publisher
from app.services.database import db
from app.services.oauth_service import oauth_service
from app.services.youtube_channel_service import youtube_channel_service

router = APIRouter(prefix="/channel", tags=["Channel & Bulk Commenting"])


class ChannelInfoRequest(BaseModel):
    channel_input: str = Field(..., description="YouTube channel URL, @handle, or channel ID")
    session_id: Optional[str] = Field(None, description="Optional authenticated session ID")


class ChannelVideosRequest(BaseModel):
    uploads_playlist_id: str = Field(..., description="Uploads playlist ID of the channel")
    max_videos: int = Field(100, ge=1, le=500, description="Maximum number of videos to fetch")
    session_id: Optional[str] = Field(None, description="Optional authenticated session ID")


class AssignCommentsRequest(BaseModel):
    videos: List[Dict[str, Any]] = Field(..., description="List of target videos")
    comments: List[str] = Field(..., description="List of unique PDF comments")
    accounts: Optional[List[Dict[str, Any]]] = Field(None, description="Optional list of accounts for multi-account distribution")
    mode: str = Field("sequential", description="'sequential' or 'random'")
    exclude_previously_commented: bool = Field(True, description="Exclude videos commented in previous runs")
    exclude_already_used_comments: bool = Field(True, description="Exclude comments already used in previous runs")


class BulkPublishRequest(BaseModel):
    session_id: Optional[str] = Field("multi_account", description="Authenticated session ID or account identifier")
    assignments: List[Dict[str, Any]] = Field(..., description="Approved list of assignments")
    delay_seconds: float = Field(5.0, ge=1.0, le=60.0, description="Delay between comments in seconds")
    pdf_filename: Optional[str] = Field(None, description="Source PDF filename")
    pdf_hash: Optional[str] = Field(None, description="SHA256 hash of PDF")


class JobControlRequest(BaseModel):
    run_id: str = Field(..., description="Run ID of the active job")
    action: str = Field(..., description="'pause', 'resume', or 'stop'")


@router.post("/info", summary="Resolve Channel Details")
async def get_channel_info(payload: ChannelInfoRequest):
    access_token = None
    if payload.session_id:
        try:
            access_token = await oauth_service.get_valid_access_token(payload.session_id.strip())
        except Exception:
            pass

    try:
        info = await youtube_channel_service.get_channel_details(
            channel_input=payload.channel_input.strip(),
            access_token=access_token,
        )
        return info
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {str(e)}")


@router.post("/videos", summary="Fetch Channel Videos")
async def get_channel_videos(payload: ChannelVideosRequest):
    access_token = None
    if payload.session_id:
        try:
            access_token = await oauth_service.get_valid_access_token(payload.session_id.strip())
        except Exception:
            pass

    try:
        videos = await youtube_channel_service.get_channel_videos(
            uploads_playlist_id=payload.uploads_playlist_id.strip(),
            max_videos=payload.max_videos,
            access_token=access_token,
        )
        return {"videos": videos, "total_fetched": len(videos)}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error: {str(e)}")


@router.post("/assign", summary="Distribute PDF Comments to Videos")
def assign_comments(payload: AssignCommentsRequest):
    try:
        if payload.accounts:
            result = comment_assignment_engine.create_multi_account_assignments(
                accounts=payload.accounts,
                videos=payload.videos,
                comments=payload.comments,
                mode=payload.mode,
                exclude_previously_commented_videos=payload.exclude_previously_commented,
                exclude_already_used_comments=payload.exclude_already_used_comments,
            )
        else:
            result = comment_assignment_engine.create_assignments(
                videos=payload.videos,
                comments=payload.comments,
                mode=payload.mode,
                exclude_previously_commented_videos=payload.exclude_previously_commented,
                exclude_already_used_comments=payload.exclude_already_used_comments,
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Assignment failed: {str(e)}")


@router.post("/bulk-publish", summary="Start Bulk Comment Publishing")
async def bulk_publish(payload: BulkPublishRequest):
    try:
        result = await comment_publisher.execute_bulk_publish(
            session_id=payload.session_id.strip(),
            assignments=payload.assignments,
            delay_seconds=payload.delay_seconds,
            pdf_filename=payload.pdf_filename,
            pdf_hash=payload.pdf_hash,
        )
        return result
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Publishing failed: {str(e)}")


@router.post("/control", summary="Pause, Resume, or Stop Publishing Job")
def control_job(payload: JobControlRequest):
    job = comment_publisher.get_job(payload.run_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No active job found for run_id '{payload.run_id}'.")

    action = payload.action.lower()
    if action == "pause":
        job.pause()
    elif action == "resume":
        job.resume()
    elif action == "stop":
        job.stop()
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action '{action}'. Use 'pause', 'resume', or 'stop'.")

    return {"run_id": payload.run_id, "status": job.status, "message": job.last_message}


@router.get("/history", summary="Get Comment History & Stats")
def get_history(
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
):
    history = db.get_history(limit=limit, status=status, source=source, search=search, run_id=run_id)
    stats = db.get_stats()
    runs = db.get_runs(limit=25)
    return {
        "history": history,
        "stats": stats,
        "runs": runs,
        "total_returned": len(history),
    }
