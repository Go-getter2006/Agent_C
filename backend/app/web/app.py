"""应用入口：uvicorn app.web.app:app"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .. import config, db, quiz
from .. import models as m
from . import auth, student

log = logging.getLogger("web")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        config.secret_key()  # 生产环境没设 SECRET_KEY 就直接报错
        db.create_all()
        with db.session_scope() as s:
            if s.scalar(select(m.QuizItem.id).limit(1)) is None:
                quiz.load_bank(s)
        yield

    app = FastAPI(title="程序设计智能体", lifespan=lifespan,
                  docs_url="/api/docs" if config.APP_ENV == "dev" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if config.APP_ENV == "dev" else None)

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.exception_handler(Exception)
    async def _server_error(_request: Request, exc: Exception):
        log.exception("接口出错")
        return JSONResponse(status_code=500, content={"error": "服务器出了点问题，稍后再试。"})

    app.include_router(auth.router)
    app.include_router(student.router)
    try:
        from . import teacher

        app.include_router(teacher.router)
    except ImportError:  # 教师端还没装上时不影响学生端
        pass

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    dist = config.FRONTEND_DIST
    if dist.exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="没有这个接口。")
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
