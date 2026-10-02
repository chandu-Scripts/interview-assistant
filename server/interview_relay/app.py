"""FastAPI wiring: friend-facing request/status/proxy endpoints + the owner-only admin dashboard."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from interview_relay import proxy
from interview_relay.auth import check_admin
from interview_relay.config import RelaySettings, load_settings
from interview_relay.db import AccessRequest, RelayDB

TEMPLATES_DIR = Path(__file__).parent / "admin_templates"


class RequestIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=300)


class RequestOut(BaseModel):
    public_id: str


class StatusOut(BaseModel):
    status: str  # "pending" | "approved" | "denied"
    token: str | None = None  # present exactly once: the first poll after approval
    budget_usd: float | None = None
    budget_scope: str | None = None


def create_app(
    db: RelayDB | None = None,
    settings: RelaySettings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """A factory (not a module-level singleton) so tests can inject an isolated in-memory db and
    a mocked http_client (httpx.AsyncClient(transport=httpx.MockTransport(...)), matching the main
    project's tests/test_llm.py pattern) instead of making real calls to OpenAI."""
    settings = settings or load_settings()
    db = db or RelayDB(settings.database_path)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    security = HTTPBasic()
    owns_client = http_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.http_client = http_client or httpx.AsyncClient(timeout=60.0)
        try:
            yield
        finally:
            if owns_client:
                await app.state.http_client.aclose()

    app = FastAPI(title="Interview Assistant Relay", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings

    def require_admin(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> None:
        if not check_admin(credentials.username, credentials.password, settings.admin_token):
            raise HTTPException(
                status_code=401,
                detail="Invalid admin credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    @app.get("/health")
    def health() -> dict:
        missing = settings.require_ready()
        return {"ok": not missing, "missing_config": missing}

    # ---- LLM proxy ----
    @app.post("/v1/chat/completions")
    async def chat_completions(
        request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> StreamingResponse:
        friend = proxy.authenticate(db, authorization)
        body = await request.json()
        stream = await proxy.handle_chat_completions(
            db, settings, app.state.http_client, friend, body
        )
        return StreamingResponse(stream, media_type="text/event-stream")

    # ---- friend-facing ----
    @app.post("/requests", response_model=RequestOut)
    def submit_request(body: RequestIn) -> RequestOut:
        req = db.create_request(body.name, body.note)
        return RequestOut(public_id=req.public_id)

    @app.get("/requests/{public_id}", response_model=StatusOut)
    def check_request(public_id: str) -> StatusOut:
        req = db.get_request(public_id)
        if req is None:
            raise HTTPException(status_code=404, detail="No such request")
        if req.status != "approved":
            return StatusOut(status=req.status)
        token = db.consume_pending_token(public_id)
        friend = next((f for f in db.list_friends() if f.request_id == req.id), None)
        return StatusOut(
            status="approved",
            token=token,  # None if already delivered once before
            budget_usd=friend.budget_usd if friend else None,
            budget_scope=friend.budget_scope if friend else None,
        )

    # ---- admin ----
    @app.get("/admin", response_class=HTMLResponse)
    def admin_dashboard(
        request: Request, _admin: Annotated[None, Depends(require_admin)]
    ) -> HTMLResponse:
        pending = [RequestView(r) for r in db.list_requests("pending")]
        friends = db.list_friends()
        flash = request.query_params.get("flash", "")
        html = templates.get_template("dashboard.html").render(
            pending=pending,
            friends=friends,
            flash=flash,
            default_budget=settings.default_budget_usd,
        )
        return HTMLResponse(html)

    @app.post("/admin/approve/{request_id}")
    def admin_approve(
        request_id: int,
        _admin: Annotated[None, Depends(require_admin)],
        budget_usd: Annotated[float, Form()] = settings.default_budget_usd,
    ) -> RedirectResponse:
        result = db.approve_request(request_id, budget_usd, settings.default_budget_scope)
        flash = (
            "Approved. They can now fetch their access from the app."
            if result
            else "That request could not be approved (already decided?)."
        )
        return _redirect(flash)

    @app.post("/admin/deny/{request_id}")
    def admin_deny(
        request_id: int, _admin: Annotated[None, Depends(require_admin)]
    ) -> RedirectResponse:
        ok = db.deny_request(request_id)
        return _redirect("Denied." if ok else "That request was already decided.")

    @app.post("/admin/revoke/{friend_id}")
    def admin_revoke(
        friend_id: int, _admin: Annotated[None, Depends(require_admin)]
    ) -> RedirectResponse:
        ok = db.revoke_friend(friend_id)
        return _redirect("Access revoked." if ok else "Already revoked or not found.")

    @app.post("/admin/reissue/{friend_id}", response_class=HTMLResponse, response_model=None)
    def admin_reissue(
        friend_id: int, _admin: Annotated[None, Depends(require_admin)]
    ) -> HTMLResponse | RedirectResponse:
        friend = db.get_friend(friend_id)
        token = db.reissue_token(friend_id)
        if token is None or friend is None:
            return _redirect("Could not reissue (revoked or not found).")
        html = templates.get_template("reissued.html").render(name=friend.name, token=token)
        return HTMLResponse(html)

    def _redirect(flash: str) -> RedirectResponse:
        return RedirectResponse(f"/admin?flash={quote(flash)}", status_code=303)

    return app


class RequestView:
    """Adapter so the Jinja template can read `.created_at_str` alongside the dataclass fields."""

    def __init__(self, req: AccessRequest) -> None:
        self._req = req
        self.created_at_str = time.strftime("%b %d %H:%M", time.localtime(req.created_at))

    def __getattr__(self, name: str):
        return getattr(self._req, name)
