"""One place that turns the services' exceptions into HTTP answers."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ebb.api.drafts import EXPIRED, DraftExpired, EmptyDraft
from ebb.config import ConfigError
from ebb.learn.service import LearnNotStarted
from ebb.llm.tutor import TutorUnavailable
from ebb.store import SubjectExists
from ebb.study import BUSY, SessionBusy


def _reply(status: int, body: dict[str, str]) -> JSONResponse:
    return JSONResponse(body, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    # Request validation already answers 422 through FastAPI's own handler.
    @app.exception_handler(TutorUnavailable)
    async def tutor_unavailable(request: Request, error: TutorUnavailable) -> JSONResponse:
        return _reply(503, {"notice": error.notice})

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, error: ConfigError) -> JSONResponse:
        return _reply(503, {"error": str(error)})

    @app.exception_handler(SubjectExists)
    async def subject_exists(request: Request, error: SubjectExists) -> JSONResponse:
        message = f"{error.name} already exists — add notes to it from its Sources list."
        return _reply(409, {"error": message})

    @app.exception_handler(SessionBusy)
    async def session_busy(request: Request, error: SessionBusy) -> JSONResponse:
        return _reply(409, {"error": BUSY})

    @app.exception_handler(LearnNotStarted)
    async def learn_not_started(request: Request, error: LearnNotStarted) -> JSONResponse:
        return _reply(409, {"error": "Start the lesson first."})

    @app.exception_handler(DraftExpired)
    async def draft_expired(request: Request, error: DraftExpired) -> JSONResponse:
        return _reply(410, {"error": EXPIRED})

    @app.exception_handler(EmptyDraft)
    async def empty_draft(request: Request, error: EmptyDraft) -> JSONResponse:
        return _reply(422, {"error": "Add a file first."})

    @app.exception_handler(LookupError)
    async def not_found(request: Request, error: LookupError) -> JSONResponse:
        return _reply(404, {"error": "Not found."})
