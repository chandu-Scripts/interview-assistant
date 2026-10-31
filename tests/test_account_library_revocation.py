"""Library commits serialize with account revocation and recheck privacy before replace."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from interview_assistant.account import context as context_module
from interview_assistant.account.context import AccountContext
from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.app.resume_library import ResumeLibrary


@pytest.fixture(params=["resume", "practice"])
def library_case(request, tmp_path, monkeypatch):
    context = AccountContext(
        tmp_path, "https://synthetic.supabase.co", "00000000-0000-4000-8000-000000000001"
    )
    monkeypatch.setattr(context_module, "_context", context)
    allowed = [True]
    path = context.account_dir / f"{request.param}.json"
    if request.param == "resume":
        library = ResumeLibrary(path, can_write=lambda: allowed[0])
        library.add("Original.txt", "Original text")

        def save():
            return library.add_copy("New.txt", "New text")

        mutations = [
            lambda: library.add("New.txt", "New text"),
            lambda: library.touch("Original.txt"),
            save,
            lambda: library.rename("Original.txt", "Renamed.txt"),
            lambda: library.delete("Original.txt"),
        ]
    else:
        library = PracticeLibrary(path, can_write=lambda: allowed[0])
        original_id = library.save(None, "Original question?", "Original answer")

        def save():
            return library.save(None, "New question?", "New answer")

        mutations = [
            save,
            lambda: library.delete(original_id),
            lambda: library.import_json('{"Imported question?": "Imported answer"}'),
            library.add_examples,
        ]
    return SimpleNamespace(
        context=context, path=path, library=library, allowed=allowed, save=save, mutations=mutations
    )


@pytest.mark.parametrize("pause_at", ["read", "replace"])
def test_admitted_library_write_finishes_before_revocation_returns(
    library_case, pause_at, monkeypatch
):
    case = library_case
    original = case.path.read_bytes()
    entered, release, revoke_started, revoked = Event(), Event(), Event(), Event()
    order = []
    read_text, replace = Path.read_text, Path.replace

    def pause():
        entered.set()
        assert release.wait(5), "Test did not release the staged library write"

    def paused_read(path, *args, **kwargs):
        result = read_text(path, *args, **kwargs)
        if path == case.path and pause_at == "read":
            pause()
        return result

    def paused_replace(path, target):
        if Path(target) == case.path:
            if pause_at == "replace":
                pause()
            result = replace(path, target)
            order.append("persisted")
            return result
        return replace(path, target)

    def revoke():
        revoke_started.set()
        case.context.revoke()
        order.append("revoked")
        revoked.set()

    monkeypatch.setattr(Path, "read_text", paused_read)
    monkeypatch.setattr(Path, "replace", paused_replace)
    with ThreadPoolExecutor(max_workers=2) as workers:
        writing = workers.submit(case.save)
        try:
            assert entered.wait(5)
            revoking = workers.submit(revoke)
            assert revoke_started.wait(5)
            revoked_during_write = revoked.wait(0.1)
        finally:
            release.set()
        writing.result(timeout=5)
        revoking.result(timeout=5)

    assert not revoked_during_write
    assert order == ["persisted", "revoked"]
    assert case.path.read_bytes() != original
    assert not case.context.active
    after_revoke = case.path.read_bytes()
    with pytest.raises(ValueError, match="account workspace"):
        case.save()
    assert case.path.read_bytes() == after_revoke


def test_queued_library_write_is_denied_after_account_revocation(library_case):
    case = library_case
    original = case.path.read_bytes()
    started = Event()

    def save():
        started.set()
        case.save()

    with ThreadPoolExecutor(max_workers=1) as workers:
        with case.context.persistence_lock:
            writing = workers.submit(save)
            assert started.wait(5)
            case.context.revoke()
        with pytest.raises(ValueError, match="account workspace"):
            writing.result(timeout=5)
    assert case.path.read_bytes() == original


def test_every_existing_library_mutation_denies_a_revoked_account(library_case):
    case = library_case
    original = case.path.read_bytes()
    case.context.revoke()
    # Even a stale UI callback that still reports writable cannot authorize a commit.
    assert case.library.writable
    for mutation in case.mutations:
        with pytest.raises(ValueError, match="account workspace"):
            mutation()
        assert case.path.read_bytes() == original


def test_privacy_change_during_serialization_discards_staged_library_file(
    library_case, monkeypatch
):
    import os

    case = library_case
    original = case.path.read_bytes()
    fsync = os.fsync

    def make_private_after_flush(fd):
        fsync(fd)
        case.allowed[0] = False

    monkeypatch.setattr(os, "fsync", make_private_after_flush)
    with pytest.raises(ValueError, match="[Ii]ncognito"):
        case.save()
    assert case.path.read_bytes() == original
    assert list(case.path.parent.glob(".*.tmp")) == []


def test_privacy_is_checked_after_a_queued_library_operation_is_admitted(library_case):
    case = library_case
    original = case.path.read_bytes()
    started = Event()

    def save():
        started.set()
        case.save()

    with ThreadPoolExecutor(max_workers=1) as workers:
        with case.context.persistence_lock:
            writing = workers.submit(save)
            assert started.wait(5)
            case.allowed[0] = False
        with pytest.raises(ValueError, match="[Ii]ncognito"):
            writing.result(timeout=5)
    assert case.context.active
    assert case.path.read_bytes() == original


def test_personal_library_keeps_existing_persistence_and_privacy_behavior(
    library_case, monkeypatch
):
    case = library_case
    monkeypatch.setattr(context_module, "_context", None)
    account_original = case.path.read_bytes()
    personal_path = case.context.root / "personal" / case.path.name
    if isinstance(case.library, ResumeLibrary):
        personal = ResumeLibrary(personal_path, can_write=lambda: case.allowed[0])
        personal.add_copy("Personal.txt", "Personal resume")

        def mutate():
            personal.delete("Personal.txt")

    else:
        personal = PracticeLibrary(personal_path, can_write=lambda: case.allowed[0])
        entry_id = personal.save(None, "Personal question?", "Personal answer")

        def mutate():
            personal.delete(entry_id)

    saved = personal_path.read_bytes()
    case.allowed[0] = False
    with pytest.raises(ValueError, match="[Ii]ncognito"):
        mutate()
    assert personal_path.read_bytes() == saved
    assert case.path.read_bytes() == account_original
