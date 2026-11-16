"""Rendered clip files: video (streamable, with HTTP range support), thumbnail, subtitles."""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models import Clip
from app.services import storage
from app.services.export import clip_basename

router = APIRouter(prefix="/clips", tags=["clips"])

DbSession = Annotated[Session, Depends(get_db)]


def _clip_file(db: Session, clip_id: uuid.UUID, attribute: str) -> tuple[Clip, Path]:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFoundError("Clip not found.")
    name = getattr(clip, attribute)
    if clip.render_status != "ready" or not name:
        raise NotFoundError("This clip has not been rendered yet.", code="clip_not_rendered")
    clips_dir = storage.job_files(clip.job_id).clips_dir.resolve()
    path = (clips_dir / name).resolve()
    if path.parent != clips_dir or not path.is_file():  # never serve outside the clip folder
        raise NotFoundError("The clip file is missing on disk.", code="clip_file_missing")
    return clip, path


def download_name(clip: Clip, extension: str) -> str:
    return f"{clip_basename(clip)}.{extension}"


@router.get("/{clip_id}/video")
def clip_video(
    clip_id: uuid.UUID,
    db: DbSession,
    download: Annotated[bool, Query(description="Send as an attachment")] = False,
) -> FileResponse:
    clip, path = _clip_file(db, clip_id, "video_file")
    if download:
        return FileResponse(path, media_type="video/mp4", filename=download_name(clip, "mp4"))
    return FileResponse(path, media_type="video/mp4")


@router.get("/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: uuid.UUID, db: DbSession) -> FileResponse:
    _, path = _clip_file(db, clip_id, "thumbnail_file")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{clip_id}/subtitles")
def clip_subtitles(clip_id: uuid.UUID, db: DbSession) -> FileResponse:
    clip, path = _clip_file(db, clip_id, "subtitle_file")
    return FileResponse(path, media_type="application/x-subrip",
                        filename=download_name(clip, "srt"))
