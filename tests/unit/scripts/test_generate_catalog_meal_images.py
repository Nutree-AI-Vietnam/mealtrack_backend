import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "generate_catalog_meal_images.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "generate_catalog_meal_images", _SCRIPT_PATH
)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
build_catalog_meal_image_prompt = _MODULE.build_catalog_meal_image_prompt


def test_build_catalog_meal_image_prompt_includes_meal_and_ingredients():
    meal = SimpleNamespace(
        name="Pho Ga",
        cuisine="vietnamese",
        ingredients=[
            SimpleNamespace(display_name="Rice noodles"),
            SimpleNamespace(display_name="Chicken"),
        ],
    )

    prompt = build_catalog_meal_image_prompt(meal)

    assert "Pho Ga" in prompt
    assert "vietnamese" in prompt
    assert "Chicken" in prompt
    assert "Rice noodles" in prompt
    assert "no text" in prompt


class _FakeUnitOfWork:
    def __init__(self, events: list[str]):
        self.events = events
        self.session = object()
        self.exit_exception_types: list[type[BaseException] | None] = []

    async def __aenter__(self):
        self.events.append("uow_enter")
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.events.append("uow_exit")
        self.exit_exception_types.append(exc_type)


def _args(*, dry_run: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        dry_run=dry_run,
        model="model",
        timeout=120,
        limit=1,
        catalog_key=[],
        include_existing=False,
        quality="medium",
        size="1024x1024",
        output_format="jpeg",
    )


def _meal() -> SimpleNamespace:
    return SimpleNamespace(
        id="catalog-1",
        catalog_key="pho-ga",
        name="Pho Ga",
        cuisine="vietnamese",
        ingredients=[SimpleNamespace(display_name="Chicken")],
    )


@pytest.mark.asyncio
async def test_run_closes_read_uow_before_remote_generation(monkeypatch):
    events: list[str] = []
    generator = SimpleNamespace(generate_url=AsyncMock(return_value="https://image"))

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", lambda: _FakeUnitOfWork(events))

    async def load_targets(*args, **kwargs):
        events.append("load")
        return [_meal()]

    async def persist(*args, **kwargs):
        events.append("persist")
        return True

    async def generate(*args, **kwargs):
        assert events == ["uow_enter", "load", "uow_exit"]
        events.append("generate")
        return "https://image"

    generator.generate_url.side_effect = generate
    monkeypatch.setattr(_MODULE, "_load_target_meals", load_targets)
    monkeypatch.setattr(_MODULE, "_persist_image_url", persist)
    monkeypatch.setattr(_MODULE, "CloudinaryImageStore", lambda: object())
    monkeypatch.setattr(
        _MODULE, "CloudflareWorkersImageGenerator", lambda **kwargs: generator
    )

    summary = await _MODULE._run(_args())

    assert summary == {"selected": 1, "updated": 1, "skipped": 0, "failed": 0}
    assert events == ["uow_enter", "load", "uow_exit", "generate", "persist"]


@pytest.mark.asyncio
async def test_run_generation_failure_does_not_open_persistence_uow(
    monkeypatch, capsys
):
    events: list[str] = []
    generator = SimpleNamespace(
        generate_url=AsyncMock(side_effect=RuntimeError("Invalid Signature secret"))
    )

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", lambda: _FakeUnitOfWork(events))
    monkeypatch.setattr(
        _MODULE,
        "_load_target_meals",
        AsyncMock(return_value=[_meal()]),
    )
    monkeypatch.setattr(_MODULE, "CloudinaryImageStore", lambda: object())
    monkeypatch.setattr(
        _MODULE, "CloudflareWorkersImageGenerator", lambda **kwargs: generator
    )
    persist = AsyncMock(return_value=True)
    monkeypatch.setattr(_MODULE, "_persist_image_url", persist)

    summary = await _MODULE._run(_args())

    assert summary == {"selected": 1, "updated": 0, "skipped": 0, "failed": 1}
    assert events == ["uow_enter", "uow_exit"]
    persist.assert_not_awaited()
    assert "cloudinary_signature_invalid" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_counts_lost_conditional_update_as_skipped(monkeypatch):
    events: list[str] = []
    generator = SimpleNamespace(generate_url=AsyncMock(return_value="https://image"))

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", lambda: _FakeUnitOfWork(events))
    monkeypatch.setattr(
        _MODULE,
        "_load_target_meals",
        AsyncMock(return_value=[_meal()]),
    )
    monkeypatch.setattr(_MODULE, "CloudinaryImageStore", lambda: object())
    monkeypatch.setattr(
        _MODULE, "CloudflareWorkersImageGenerator", lambda **kwargs: generator
    )
    monkeypatch.setattr(_MODULE, "_persist_image_url", AsyncMock(return_value=False))

    summary = await _MODULE._run(_args())

    assert summary == {"selected": 1, "updated": 0, "skipped": 1, "failed": 0}


@pytest.mark.asyncio
async def test_set_image_url_bumps_updated_at():
    captured: dict[str, object] = {}

    class _Session:
        async def execute(self, statement):
            captured["statement"] = statement
            return SimpleNamespace(rowcount=1)

        async def flush(self):
            captured["flushed"] = True

    persisted = await _MODULE._set_image_url(
        _Session(),
        "catalog-1",
        "https://image.test/pho.jpg",
        include_existing=False,
    )

    assert persisted is True
    compiled = captured["statement"].compile()
    assert "updated_at" in str(compiled)
    assert compiled.params["image_url"] == "https://image.test/pho.jpg"


@pytest.mark.asyncio
async def test_persist_image_url_uses_a_fresh_short_lived_uow(monkeypatch):
    events: list[str] = []
    seen_sessions: list[object] = []

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", lambda: _FakeUnitOfWork(events))

    async def set_image_url(session, *args, **kwargs):
        seen_sessions.append(session)
        events.append("set")
        return True

    monkeypatch.setattr(_MODULE, "_set_image_url", set_image_url)

    persisted = await _MODULE._persist_image_url(
        "catalog-1",
        "https://image",
        include_existing=False,
    )

    assert persisted is True
    assert len(seen_sessions) == 1
    assert events == ["uow_enter", "set", "uow_exit"]


@pytest.mark.asyncio
async def test_run_persistence_failure_exits_fresh_uow_and_counts_failure(monkeypatch):
    events: list[str] = []
    units_of_work: list[_FakeUnitOfWork] = []
    generator = SimpleNamespace(generate_url=AsyncMock(return_value="https://image"))

    def make_uow() -> _FakeUnitOfWork:
        uow = _FakeUnitOfWork(events)
        units_of_work.append(uow)
        return uow

    async def fail_persistence(*args, **kwargs):
        events.append("set")
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", make_uow)
    monkeypatch.setattr(
        _MODULE,
        "_load_target_meals",
        AsyncMock(return_value=[_meal()]),
    )
    monkeypatch.setattr(_MODULE, "CloudinaryImageStore", lambda: object())
    monkeypatch.setattr(
        _MODULE, "CloudflareWorkersImageGenerator", lambda **kwargs: generator
    )
    monkeypatch.setattr(_MODULE, "_set_image_url", fail_persistence)

    summary = await _MODULE._run(_args())

    assert summary == {"selected": 1, "updated": 0, "skipped": 0, "failed": 1}
    assert events == ["uow_enter", "uow_exit", "uow_enter", "set", "uow_exit"]
    assert units_of_work[1].exit_exception_types == [RuntimeError]


@pytest.mark.asyncio
async def test_run_dry_run_does_not_initialize_remote_generator(monkeypatch):
    events: list[str] = []

    monkeypatch.setattr(_MODULE, "AsyncUnitOfWork", lambda: _FakeUnitOfWork(events))
    monkeypatch.setattr(
        _MODULE,
        "_load_target_meals",
        AsyncMock(return_value=[_meal()]),
    )
    monkeypatch.setattr(
        _MODULE,
        "CloudflareWorkersImageGenerator",
        lambda **kwargs: pytest.fail("generator must not initialize for dry-run"),
    )

    summary = await _MODULE._run(_args(dry_run=True))

    assert summary == {"selected": 1, "updated": 0, "skipped": 0, "failed": 0}
