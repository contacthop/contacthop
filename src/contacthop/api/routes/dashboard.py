"""The command center: a self-contained operational dashboard at /dashboard.

The page is a static shell — all data flows through the same authenticated
/v1 API the SDK uses (the page asks for the API key when one is configured),
so serving the shell itself needs no auth.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = (
    Path(__file__).parent.parent.parent / "static" / "dashboard.html"
)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return DASHBOARD_HTML.read_text(encoding="utf-8")
