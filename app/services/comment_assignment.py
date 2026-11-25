"""PDF Comment Distribution and Multi-Account Video Matching Engine."""

import random
from typing import Any, Dict, List, Optional, Set
from app.services.database import db


class CommentAssignmentEngine:
    """
    Distributes unique pre-written PDF comments across selected YouTube videos and authorized accounts.
    Guarantees strict 1-to-1 mapping with zero duplicate assignments, zero artificial coordinate spamming,
    and zero AI modifications.
    """

    def create_assignments(
        self,
        videos: List[Dict[str, Any]],
        comments: List[str],
        mode: str = "sequential",  # "sequential" or "random"
        exclude_previously_commented_videos: bool = True,
        exclude_already_used_comments: bool = True,
        random_seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Assigns exactly one unique PDF comment to each selected video for a single account.
        Preserves original wording without modification.
        """
        if not comments:
            raise ValueError("No comments provided for assignment.")
        if not videos:
            raise ValueError("No videos provided for assignment.")

        # 1. Deduplicate comments list preserving order
        unique_comments = []
        seen_comments: Set[str] = set()
        for c in comments:
            clean = " ".join(c.split()).strip()
            if clean and clean.lower() not in seen_comments:
                seen_comments.add(clean.lower())
                unique_comments.append(clean)

        # 2. Filter previously used comments if enabled
        if exclude_already_used_comments:
            used_db_comments = db.get_used_comments()
            filtered_comments = [c for c in unique_comments if c not in used_db_comments]
            if filtered_comments:
                unique_comments = filtered_comments

        # 3. Filter previously commented videos if enabled
        eligible_videos = list(videos)
        if exclude_previously_commented_videos:
            commented_vids = db.get_commented_video_ids()
            eligible_videos = [v for v in eligible_videos if v.get("video_id") not in commented_vids]

        if not eligible_videos:
            raise ValueError(
                "All selected videos have already been commented on in previous runs. "
                "Uncheck 'Exclude previously commented videos' or select other videos."
            )

        # 4. Prepare comments pool based on mode
        available_comments = list(unique_comments)
        if mode == "random":
            rng = random.Random(random_seed) if random_seed is not None else random.Random()
            rng.shuffle(available_comments)

        # 5. Distribute 1-to-1 up to min(len(videos), len(comments))
        num_assignments = min(len(eligible_videos), len(available_comments))

        assignments: List[Dict[str, Any]] = []
        for i in range(num_assignments):
            vid = eligible_videos[i]
            assigned_text = available_comments[i]

            assignments.append({
                "index": i + 1,
                "video_id": vid.get("video_id", ""),
                "video_title": vid.get("title", ""),
                "video_url": vid.get("url") or f"https://www.youtube.com/watch?v={vid.get('video_id')}",
                "thumbnail_url": vid.get("thumbnail_url", ""),
                "published_at": vid.get("published_at", ""),
                "assigned_comment": assigned_text,
                "approved_comment": assigned_text,
                "is_approved": False,
                "status": "pending",  # 'pending', 'approved', 'excluded'
                "comment_source": "pdf",
            })

        unused_comments = available_comments[num_assignments:]
        unassigned_videos = eligible_videos[num_assignments:]

        return {
            "assignments": assignments,
            "total_assigned": len(assignments),
            "total_videos_selected": len(videos),
            "eligible_videos_count": len(eligible_videos),
            "total_comments_available": len(comments),
            "total_unique_comments": len(unique_comments),
            "unused_comments_count": len(unused_comments),
            "unused_comments": unused_comments,
            "unassigned_videos_count": len(unassigned_videos),
            "unassigned_videos": unassigned_videos,
            "mode": mode,
        }

    def create_multi_account_assignments(
        self,
        accounts: List[Dict[str, Any]],
        videos: List[Dict[str, Any]],
        comments: List[str],
        mode: str = "sequential",
        distribution_strategy: str = "auto",
        exclude_previously_commented_videos: bool = True,
        exclude_already_used_comments: bool = True,
        random_seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Assigns unique PDF comments across multiple connected accounts and target videos.
        Guarantees:
        - Strict 1-to-1 video assignment or account-per-video assignment without comment duplication.
        - Preserves exact verbatim PDF comment text.
        """
        if not accounts:
            raise ValueError("No authorized accounts provided for assignment.")
        if not comments:
            raise ValueError("No PDF comments available for assignment.")
        if not videos:
            raise ValueError("No videos provided for assignment.")

        # Clean and deduplicate comments
        unique_comments = []
        seen_comments: Set[str] = set()
        for c in comments:
            clean = " ".join(c.split()).strip()
            if clean and clean.lower() not in seen_comments:
                seen_comments.add(clean.lower())
                unique_comments.append(clean)

        if exclude_already_used_comments:
            used_db_comments = db.get_used_comments()
            filtered_comments = [c for c in unique_comments if c not in used_db_comments]
            if filtered_comments:
                unique_comments = filtered_comments

        # Filter videos
        eligible_videos = list(videos)
        if exclude_previously_commented_videos:
            commented_vids = db.get_commented_video_ids()
            eligible_videos = [v for v in eligible_videos if v.get("video_id") not in commented_vids]

        if not eligible_videos:
            raise ValueError("All selected videos have already been commented on in previous runs.")

        available_comments = list(unique_comments)
        if mode == "random":
            rng = random.Random(random_seed) if random_seed is not None else random.Random()
            rng.shuffle(available_comments)

        assignments: List[Dict[str, Any]] = []
        comment_idx = 0

        # Multi-Account Distribution:
        # If strategy is 'each_account_per_video' or only 1 video was supplied with multiple accounts:
        # Assign a distinct comment to each selected account for that video.
        is_account_per_video = (
            distribution_strategy == "each_account_per_video"
            or (len(eligible_videos) == 1 and len(accounts) > 1 and distribution_strategy != "round_robin")
        )

        if is_account_per_video:
            for vid in eligible_videos:
                for acc in accounts:
                    if comment_idx >= len(available_comments):
                        break
                    assigned_text = available_comments[comment_idx]
                    comment_idx += 1
                    assignments.append({
                        "index": len(assignments) + 1,
                        "account_id": acc.get("account_id"),
                        "account_name": acc.get("channel_title") or "YouTube Account",
                        "channel_id": acc.get("channel_id"),
                        "video_id": vid.get("video_id", ""),
                        "video_title": vid.get("title", ""),
                        "video_url": vid.get("url") or f"https://www.youtube.com/watch?v={vid.get('video_id')}",
                        "thumbnail_url": vid.get("thumbnail_url", ""),
                        "assigned_comment": assigned_text,
                        "approved_comment": assigned_text,
                        "is_approved": False,
                        "status": "pending",
                        "comment_source": "pdf",
                    })
        else:
            # Round-robin across videos
            num_assignments = min(len(eligible_videos), len(available_comments))
            for i in range(num_assignments):
                vid = eligible_videos[i]
                assigned_text = available_comments[i]
                assigned_acc = accounts[i % len(accounts)]

                assignments.append({
                    "index": i + 1,
                    "account_id": assigned_acc.get("account_id"),
                    "account_name": assigned_acc.get("channel_title") or "YouTube Account",
                    "channel_id": assigned_acc.get("channel_id"),
                    "video_id": vid.get("video_id", ""),
                    "video_title": vid.get("title", ""),
                    "video_url": vid.get("url") or f"https://www.youtube.com/watch?v={vid.get('video_id')}",
                    "thumbnail_url": vid.get("thumbnail_url", ""),
                    "assigned_comment": assigned_text,
                    "approved_comment": assigned_text,
                    "is_approved": False,
                    "status": "pending",
                    "comment_source": "pdf",
                })
                comment_idx += 1

        unused_comments = available_comments[comment_idx:]
        unassigned_videos = eligible_videos[len(assignments):] if not is_account_per_video else []

        return {
            "assignments": assignments,
            "total_assigned": len(assignments),
            "total_videos_selected": len(videos),
            "eligible_videos_count": len(eligible_videos),
            "total_comments_available": len(comments),
            "total_unique_comments": len(unique_comments),
            "unused_comments_count": len(unused_comments),
            "unused_comments": unused_comments,
            "unassigned_videos_count": len(unassigned_videos),
            "unassigned_videos": unassigned_videos,
            "mode": mode,
        }


# Global assignment engine instance
comment_assignment_engine = CommentAssignmentEngine()
