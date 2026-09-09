# -*- coding: utf-8 -*-
"""注册失败时保存当前浏览器界面截图，便于事后对照页面状态。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core import db

logger = logging.getLogger(__name__)


def capture_registration_failure(source: Any, *, reason: str = "") -> str | None:
    """把当前浏览器界面存成 PNG，并挂到当前任务。截图失败不影响主流程。"""
    try:
        png = _png_bytes(source)
        if not png:
            return None
        job = _active_job()
        path = _screenshot_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        if job is not None:
            db.update_job(int(job["id"]), screenshot_path=str(path))
        hint = f"（{reason}）" if reason else ""
        logger.warning("[失败截图] 已保存%s：%s", hint, path)
        return str(path)
    except Exception as exc:
        logger.warning("[失败截图] 保存失败：%s: %s", type(exc).__name__, str(exc)[:180])
        return None


def _png_bytes(source: Any) -> bytes | None:
    if source is None:
        return None
    getter = getattr(source, "get_screenshot_as_png", None)
    if callable(getter):
        data = getter()
        if data:
            return bytes(data)
    page = getattr(source, "page", source)
    shot = getattr(page, "screenshot", None)
    if not callable(shot):
        return None
    try:
        data = shot(type="png")
    except TypeError:
        data = shot()
    return bytes(data) if data else None


def _active_job() -> dict | None:
    try:
        from core import registration_service as svc
        job_id = getattr(svc._THREAD_CTX, "job_id", None)
        if job_id is None:
            return None
        return db.get_job(int(job_id))
    except Exception:
        return None


def _screenshot_path(job: dict | None) -> Path:
    if job:
        log_file = str(job.get("log_file") or "").strip()
        if log_file:
            return Path(log_file).with_suffix(".png")
        job_uuid = str(job.get("job_uuid") or "").strip()
        if job_uuid:
            return db.log_dir() / f"{job_uuid}.png"
    return db.log_dir() / "fail-screenshot.png"


def resolve_job_screenshot_path(job: dict | None) -> Path | None:
    """只允许返回任务日志目录内的截图文件。"""
    if not job:
        return None
    raw = str(job.get("screenshot_path") or "").strip()
    if not raw:
        log_file = str(job.get("log_file") or "").strip()
        raw = str(Path(log_file).with_suffix(".png")) if log_file else ""
    if not raw:
        return None
    path = Path(raw).resolve()
    try:
        path.relative_to(db.log_dir().resolve())
    except ValueError:
        return None
    return path if path.is_file() else None
