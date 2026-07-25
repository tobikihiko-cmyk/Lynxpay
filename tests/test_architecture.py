from collections import Counter
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROUTER_LINE_LIMIT = 800


def test_legacy_router_aggregator_is_removed() -> None:
    assert not (ROOT / "app" / "router.py").exists()
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "from app.routers import router as lynxpay_router" in main_source


def test_domain_routers_remain_reviewable() -> None:
    oversized = {}
    for path in (ROOT / "app" / "routers").glob("*.py"):
        if path.name == "__init__.py":
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > DOMAIN_ROUTER_LINE_LIMIT:
            oversized[path.name] = line_count

    assert oversized == {}, (
        f"Domain routers must remain under {DOMAIN_ROUTER_LINE_LIMIT} lines; "
        f"split these modules: {oversized}"
    )


def test_no_duplicate_method_and_path_registrations() -> None:
    registrations = Counter(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )
    duplicates = {
        f"{method} {path}": count
        for (method, path), count in registrations.items()
        if count > 1
    }

    assert duplicates == {}, f"Duplicate API route registrations found: {duplicates}"
