from __future__ import annotations

import subprocess
import sys
import types
from urllib.parse import urlparse


def test_first_class_project_crud_and_path_resolution(monkeypatch, tmp_path):
    from api import projects_db_adapter as adapter

    hermes_state = types.ModuleType("hermes_state")
    hermes_state.apply_wal_with_fallback = lambda conn, **_kwargs: conn.execute(
        "PRAGMA journal_mode=WAL"
    )
    monkeypatch.setitem(sys.modules, "hermes_state", hermes_state)

    profile_home = tmp_path / "hermes"
    monkeypatch.setattr(
        "api.profiles.get_hermes_home_for_profile",
        lambda _name: profile_home,
    )
    monkeypatch.setattr("api.profiles._is_root_profile", lambda name: name == "default")

    primary = tmp_path / "repo"
    secondary = tmp_path / "notes"
    primary.mkdir()
    secondary.mkdir()

    created = adapter.create_project_in_db(
        profile_name="default",
        name="Aligned Project",
        slug="aligned-project",
        description="One source of truth",
        color="#123456",
        folders=[str(primary)],
        primary_path=str(primary),
    )

    assert created["project_id"] == "aligned-project"
    assert created["hermes_project_id"].startswith("p_")
    assert created["primary_path"] == str(primary)
    assert adapter.project_for_path_from_db(
        str(primary / "src"),
        profile_name="default",
    )["hermes_project_id"] == created["hermes_project_id"]

    updated = adapter.update_project_in_db(
        created["hermes_project_id"],
        profile_name="default",
        changes={"name": "Renamed", "board_slug": "delivery"},
    )
    assert updated["name"] == "Renamed"
    assert updated["board_slug"] == "delivery"

    updated = adapter.change_project_folder_in_db(
        created["hermes_project_id"],
        profile_name="default",
        action="add",
        path=str(secondary),
        is_primary=True,
    )
    assert updated["primary_path"] == str(secondary)
    assert {folder["path"] for folder in updated["folders"]} == {
        str(primary),
        str(secondary),
    }

    archived = adapter.archive_project_in_db(
        created["hermes_project_id"],
        profile_name="default",
        archived=True,
    )
    assert archived["project_id"] == "aligned-project"
    assert adapter.load_projects_from_db(profile_name="default") == []

    restored = adapter.archive_project_in_db(
        created["hermes_project_id"],
        profile_name="default",
        archived=False,
    )
    assert restored["project_id"] == "aligned-project"
    assert adapter.delete_project_in_db(
        created["hermes_project_id"],
        profile_name="default",
    ) is True
    assert adapter.load_projects_from_db(profile_name="default") == []


def test_session_history_persists_project_and_git_provenance(tmp_path, monkeypatch):
    from api import models

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", tmp_path / "sessions" / "_index.json")
    models.SESSION_DIR.mkdir()
    session = models.new_session(
        workspace=str(repo),
        model="test-model",
        profile="default",
        project_id="aligned-project",
    )
    session.title = "Project chat"
    session.messages = [{"role": "user", "content": "hello"}]
    session.save()

    loaded = models.Session.load(session.session_id)
    compact = loaded.compact()
    assert compact["project_id"] == "aligned-project"
    assert compact["git_repo_root"] == str(repo)
    assert compact["git_head_at_start"] == head
    assert compact["git_head_sha"] == head
    assert compact["git_branch"]


def test_unassigned_chat_history_is_projected_from_workspace(monkeypatch, tmp_path):
    from api.models import enrich_session_project_membership

    root = tmp_path / "project"
    nested = root / "packages" / "web"
    nested.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(
        "api.projects_db_adapter.load_projects_from_db",
        lambda **kwargs: calls.append(kwargs) or [{
            "project_id": "web-project",
            "hermes_project_id": "p_12345678",
            "profile": "default",
            "folders": [{"path": str(root)}],
        }],
    )
    rows = [
        {"session_id": "in-project", "profile": "default", "workspace": str(nested)},
        {"session_id": "manual", "profile": "default", "workspace": str(nested), "project_id": "kept"},
    ]

    assert enrich_session_project_membership(rows) == [
        {
            "session_id": "in-project",
            "profile": "default",
            "workspace": str(nested),
            "project_id": "web-project",
            "hermes_project_id": "p_12345678",
        },
        {
            "session_id": "manual",
            "profile": "default",
            "workspace": str(nested),
            "project_id": "kept",
        },
    ]
    assert calls == [{"profile_name": "default"}]


def test_restore_route_can_address_an_archived_first_class_project(monkeypatch):
    import api.routes as routes

    calls = []
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"project_id": "p_12345678", "profile": "default"},
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)
    monkeypatch.setattr(
        "api.projects_db_adapter.archive_project_in_db",
        lambda project_id, **kwargs: calls.append((project_id, kwargs)) or {
            "project_id": "restored",
            "hermes_project_id": project_id,
            "source": "hermes",
        },
    )

    payload = routes.handle_post(object(), urlparse("/api/projects/restore"))

    assert payload["project"]["project_id"] == "restored"
    assert calls == [
        ("p_12345678", {"profile_name": "default", "archived": False}),
    ]


def test_legacy_project_write_never_serializes_merged_database_rows(monkeypatch):
    import api.routes as routes

    legacy = {"project_id": "legacy", "name": "Legacy", "profile": "default"}
    db_row = {
        "project_id": "first-class",
        "hermes_project_id": "p_12345678",
        "name": "Hermes",
        "profile": "default",
        "source": "hermes",
    }
    saved = []

    def load_projects(**kwargs):
        rows = [dict(legacy)]
        if kwargs.get("include_db"):
            rows.append(dict(db_row))
        return rows

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(routes, "load_projects", load_projects)
    monkeypatch.setattr(routes, "save_projects", lambda rows: saved.append(rows))
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "project_id": "legacy",
            "profile": "default",
            "name": "Renamed",
        },
    )
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)

    payload = routes.handle_post(object(), urlparse("/api/projects/rename"))

    assert payload["project"]["name"] == "Renamed"
    assert saved == [[{"project_id": "legacy", "name": "Renamed", "profile": "default"}]]
    assert all(row.get("source") != "hermes" for row in saved[0])
