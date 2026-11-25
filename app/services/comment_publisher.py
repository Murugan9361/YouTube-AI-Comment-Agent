"""Safe Bulk Comment Publishing Engine with Pause, Resume, and Stop controls."""

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from app.services.database import db
from app.services.youtube_service import youtube_service


class PublishingJobState:
    """Maintains active state of a bulk publishing job for real-time tracking and control."""

    def __init__(self, run_id: str, total_items: int) -> None:
        self.run_id = run_id
        self.total_items = total_items
        self.current_index = 0
        self.published_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.status = "running"  # 'running', 'paused', 'stopped', 'completed'
        self.current_video_title = ""
        self.last_message = "Starting publishing job..."
        self.results: List[Dict[str, Any]] = []
        self._is_paused = False
        self._is_stopped = False

    def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"
            self._is_paused = True
            self.last_message = "Publishing paused by user."

    def resume(self) -> None:
        if self.status == "paused":
            self.status = "running"
            self._is_paused = False
            self.last_message = "Publishing resumed."

    def stop(self) -> None:
        self.status = "stopped"
        self._is_stopped = True
        self._is_paused = False
        self.last_message = "Publishing stopped by user."

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped


class BulkCommentPublisher:
    """Executes bulk comment publishing with rate limiting, error resilience, and progress tracking."""

    def __init__(self) -> None:
        self.active_jobs: Dict[str, PublishingJobState] = {}

    def get_job(self, run_id: str) -> Optional[PublishingJobState]:
        return self.active_jobs.get(run_id)

    async def execute_bulk_publish(
        self,
        session_id: str,
        assignments: List[Dict[str, Any]],
        delay_seconds: float = 5.0,
        run_id: Optional[str] = None,
        pdf_filename: Optional[str] = None,
        pdf_hash: Optional[str] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Sequentially publishes comments to YouTube videos.
        Respects rate limits, updates database records, and supports pause/resume/stop.
        """
        if not assignments:
            raise ValueError("No assignments provided to publish.")

        active_run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        job_state = PublishingJobState(run_id=active_run_id, total_items=len(assignments))
        self.active_jobs[active_run_id] = job_state

        # Initialize run in database
        db.create_run(
            run_id=active_run_id,
            mode="pdf_channel",
            pdf_filename=pdf_filename,
            pdf_hash=pdf_hash,
            total_videos=len(assignments),
            total_comments=len(assignments),
        )

        for idx, item in enumerate(assignments):
            # Check for stop request
            if job_state.is_stopped:
                job_state.last_message = f"Process stopped at video {idx + 1} of {len(assignments)}."
                break

            # Handle pause
            while job_state.is_paused:
                if job_state.is_stopped:
                    break
                job_state.last_message = "Paused. Waiting for resume..."
                if progress_callback:
                    progress_callback(self._build_progress_dict(job_state))
                await asyncio.sleep(0.5)

            if job_state.is_stopped:
                break

            job_state.current_index = idx + 1
            video_id = item.get("video_id", "")
            video_title = item.get("video_title", "Untitled Video")
            video_url = item.get("video_url") or f"https://www.youtube.com/watch?v={video_id}"
            comment_text = item.get("assigned_comment", "")

            job_state.current_video_title = video_title
            job_state.last_message = f"Publishing to video {idx + 1}/{len(assignments)}: {video_title[:40]}..."

            if progress_callback:
                progress_callback(self._build_progress_dict(job_state))

            # Attempt to publish comment via account-specific or session-based publishing
            try:
                account_id = item.get("account_id")
                approved_text = item.get("approved_comment") or comment_text
                if account_id:
                    pub_result = await youtube_service.publish_comment_for_account(
                        account_id=account_id,
                        video_url=video_url,
                        comment_text=comment_text,
                        approved_comment=approved_text,
                        run_id=active_run_id,
                        pdf_filename=pdf_filename,
                        pdf_hash=pdf_hash,
                    )
                else:
                    pub_result = await youtube_service.publish_comment(
                        session_id=session_id,
                        video_url=video_url,
                        comment_text=comment_text,
                    )
                    # Record in database for session publish
                    db.log_comment(
                        run_id=active_run_id,
                        video_id=video_id,
                        video_title=video_title,
                        video_url=video_url,
                        assigned_comment=comment_text,
                        approved_comment=approved_text,
                        publishing_status="success",
                        comment_source="pdf",
                        published_comment_id=pub_result.get("comment_id", ""),
                        pdf_filename=pdf_filename,
                        pdf_hash=pdf_hash,
                    )

                comment_id = pub_result.get("comment_id", "")
                job_state.published_count += 1
                item["status"] = "success"
                item["comment_id"] = comment_id
                item["error"] = None

            except Exception as e:
                err_msg = str(e)
                job_state.failed_count += 1
                item["status"] = "failed"
                item["error"] = err_msg

                # Record failure in database
                db.log_comment(
                    run_id=active_run_id,
                    video_id=video_id,
                    video_title=video_title,
                    video_url=video_url,
                    assigned_comment=comment_text,
                    publishing_status="failed",
                    comment_source="pdf",
                    error_details=err_msg,
                    pdf_filename=pdf_filename,
                    pdf_hash=pdf_hash,
                )

                # If quota exceeded, halt execution immediately to avoid spamming
                if "quota" in err_msg.lower():
                    job_state.last_message = "YouTube API quota exceeded. Stopping bulk run."
                    job_state.status = "stopped"
                    break

            # Rate limiting delay between video comment threads
            if idx < len(assignments) - 1 and not job_state.is_stopped:
                delay_remaining = float(delay_seconds)
                while delay_remaining > 0:
                    if job_state.is_stopped or job_state.is_paused:
                        break
                    step = min(0.5, delay_remaining)
                    await asyncio.sleep(step)
                    delay_remaining -= step

        # Mark completion
        if not job_state.is_stopped:
            job_state.status = "completed"
            job_state.last_message = (
                f"Finished: {job_state.published_count} published, "
                f"{job_state.failed_count} failed out of {len(assignments)}."
            )

        db.update_run_status(
            run_id=active_run_id,
            status=job_state.status,
            published_count=job_state.published_count,
            failed_count=job_state.failed_count,
            skipped_count=job_state.skipped_count,
        )

        final_summary = self._build_progress_dict(job_state)
        if progress_callback:
            progress_callback(final_summary)

        return final_summary

    def _build_progress_dict(self, job_state: PublishingJobState) -> Dict[str, Any]:
        pct = (job_state.current_index / job_state.total_items) if job_state.total_items > 0 else 0.0
        return {
            "run_id": job_state.run_id,
            "status": job_state.status,
            "current_index": job_state.current_index,
            "total_items": job_state.total_items,
            "progress_percent": min(1.0, max(0.0, pct)),
            "published_count": job_state.published_count,
            "failed_count": job_state.failed_count,
            "skipped_count": job_state.skipped_count,
            "current_video_title": job_state.current_video_title,
            "last_message": job_state.last_message,
        }


# Global bulk publisher instance
comment_publisher = BulkCommentPublisher()
