"""Hermes Project store adapter.

The per-profile ``projects.db`` is authoritative for first-class Hermes
Projects.  This module owns profile/path resolution, SQLite connection
semantics, and conversion to the WebUI's compatibility shape so callers do not
need to know about the agent database schema.
"""
from __future__ import annotations

from contextlib import closing
import importlib
import logging
import sqlite3
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _active_profile_name(profile_name: str | None = None) -> str:
    if profile_name:
        return str(profile_name).strip() or "default"
    try:
        from api.profiles import get_active_profile_name

        return get_active_profile_name() or "default"
    except Exception:
        return "default"


def _store(profile_name: str | None, *, require_existing: bool) -> tuple[object, str, Path] | None:
    try:
        projects_db = importlib.import_module("hermes_cli.projects_db")
        from api.profiles import (
            _PROFILE_ID_RE,
            _is_root_profile,
            get_hermes_home_for_profile,
        )

        profile = _active_profile_name(profile_name)
        if not _is_root_profile(profile) and _PROFILE_ID_RE.fullmatch(profile) is None:
            return None
        db_path = Path(get_hermes_home_for_profile(profile)) / "projects.db"
    except Exception:
        return None
    if require_existing and not db_path.exists():
        return None
    return projects_db, profile, db_path


def _project_to_webui_dict(project, profile_name: str) -> dict:
    row = {
        # Keep the slug as the compatibility id used by existing WebUI
        # sessions, while exposing Hermes' canonical p_* id explicitly.
        "project_id": project.slug,
        "hermes_project_id": getattr(project, "id", None),
        "slug": project.slug,
        "name": project.name,
        "description": getattr(project, "description", None),
        "icon": getattr(project, "icon", None),
        "color": project.color,
        "board_slug": getattr(project, "board_slug", None),
        "profile": profile_name,
        "source": "hermes",
    }
    created_at = getattr(project, "created_at", None)
    if created_at is not None:
        row["created_at"] = created_at
    primary_path = getattr(project, "primary_path", None)
    if primary_path is not None:
        row["primary_path"] = primary_path
        # Existing WebUI new-chat code understands this field.  A first-class
        # project's primary folder is the same concept.
        row["default_workspace"] = primary_path
    folders = getattr(project, "folders", None)
    if folders is not None:
        row["folders"] = [
            folder.to_dict() if hasattr(folder, "to_dict") else folder
            for folder in folders
        ]
    if not row["hermes_project_id"]:
        row.pop("hermes_project_id")
    return row


def load_projects_from_db(*, profile_name: str | None = None) -> list[dict] | None:
    """Return active projects without mutating Project rows or schema.

    SQLite owns locking and journal consistency.  In particular, do not use
    ``immutable=1`` for a live database: it bypasses rollback-journal locks and
    can expose uncommitted data.  SQLite may maintain its normal transient
    WAL/SHM coordination files while this lock-aware read is open.
    """
    store = _store(profile_name, require_existing=True)
    if store is None:
        return None
    projects_db, profile, db_path = store
    try:
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(db_uri, uri=True, timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            return [
                _project_to_webui_dict(project, profile)
                for project in projects_db.list_projects(conn)
                if not getattr(project, "archived", False)
            ]
    except Exception:
        logger.debug("Unable to read Hermes projects database", exc_info=True)
        return None


def _write(
    profile_name: str | None,
    operation: Callable[[object, sqlite3.Connection, str], dict | bool | None],
) -> dict | bool | None:
    store = _store(profile_name, require_existing=False)
    if store is None:
        return None
    projects_db, profile, db_path = store
    connect = getattr(projects_db, "connect", None)
    if not callable(connect):
        return None
    try:
        with closing(connect(db_path)) as conn:
            return operation(projects_db, conn, profile)
    except (AttributeError, ImportError, OSError, sqlite3.Error, TypeError, ValueError):
        logger.debug("Unable to mutate Hermes projects database", exc_info=True)
        return False


def create_project_in_db(
    *,
    profile_name: str | None,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    icon: str | None = None,
    color: str | None = None,
    board_slug: str | None = None,
    folders: list[str] | None = None,
    primary_path: str | None = None,
) -> dict | bool | None:
    def operation(projects_db, conn, profile):
        create = getattr(projects_db, "create_project", None)
        get = getattr(projects_db, "get_project", None)
        if not callable(create) or not callable(get):
            return None
        project_id = create(
            conn,
            name=name,
            slug=slug,
            description=description,
            icon=icon,
            color=color,
            board_slug=board_slug,
            folders=folders or [],
            primary_path=primary_path,
        )
        return _project_to_webui_dict(get(conn, project_id), profile)

    return _write(profile_name, operation)


def update_project_in_db(
    project_id: str,
    *,
    profile_name: str | None,
    changes: dict,
) -> dict | bool | None:
    allowed = {"name", "description", "icon", "color", "board_slug"}
    patch = {key: value for key, value in changes.items() if key in allowed}

    def operation(projects_db, conn, profile):
        project = projects_db.get_project(conn, project_id)
        if project is None:
            return False
        if patch and not projects_db.update_project(conn, project.id, **patch):
            return False
        return _project_to_webui_dict(projects_db.get_project(conn, project.id), profile)

    return _write(profile_name, operation)


def delete_project_in_db(project_id: str, *, profile_name: str | None) -> bool | None:
    def operation(projects_db, conn, _profile):
        project = projects_db.get_project(conn, project_id)
        return bool(project and projects_db.delete_project(conn, project.id))

    return _write(profile_name, operation)


def archive_project_in_db(
    project_id: str,
    *,
    profile_name: str | None,
    archived: bool,
) -> dict | bool | None:
    def operation(projects_db, conn, profile):
        project = projects_db.get_project(conn, project_id)
        if project is None:
            return False
        fn = projects_db.archive_project if archived else projects_db.restore_project
        if not fn(conn, project.id):
            return False
        return _project_to_webui_dict(projects_db.get_project(conn, project.id), profile)

    return _write(profile_name, operation)


def change_project_folder_in_db(
    project_id: str,
    *,
    profile_name: str | None,
    action: str,
    path: str,
    label: str | None = None,
    is_primary: bool = False,
) -> dict | bool | None:
    def operation(projects_db, conn, profile):
        project = projects_db.get_project(conn, project_id)
        if project is None:
            return False
        if action == "add":
            projects_db.add_folder(
                conn,
                project.id,
                path,
                label=label,
                is_primary=is_primary,
            )
        elif action == "remove":
            if not projects_db.remove_folder(conn, project.id, path):
                return False
        elif action == "primary":
            if not projects_db.set_primary(conn, project.id, path):
                return False
        else:
            raise ValueError("unsupported project folder action")
        return _project_to_webui_dict(projects_db.get_project(conn, project.id), profile)

    return _write(profile_name, operation)


def project_for_path_from_db(path: str, *, profile_name: str | None = None) -> dict | None:
    store = _store(profile_name, require_existing=True)
    if store is None:
        return None
    projects_db, profile, db_path = store
    resolve = getattr(projects_db, "project_for_path", None)
    if not callable(resolve):
        return None
    try:
        db_uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(db_uri, uri=True, timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            project = resolve(conn, path)
            return _project_to_webui_dict(project, profile) if project else None
    except Exception:
        logger.debug("Unable to resolve Hermes project for path", exc_info=True)
        return None
