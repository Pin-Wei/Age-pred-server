#!/usr/bin/env python

import json
import logging
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from server import (
    Config, 
    get_config, 
    setup_logger, 
    register_exception_handlers, 
    authenticate_gitlab
)
from uvicorn_config import LOGGING_CONFIG


logger = logging.getLogger(__name__)


class SubjectDownloadRequest(BaseModel):
    subject_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(__name__)
    cfg = get_config()
    logger.info("get_integrated_result service initialized.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, lifespan=lifespan)
    register_exception_handlers(app, logger)

    @app.post("/get_integrated_result")
    def get_integrated_result(
        request: SubjectDownloadRequest,
        token: str = Depends(authenticate_gitlab),
        config: Config = Depends(get_config),
    ):
        subject_id = request.subject_id
        logger.info(f"Received request to get integrated result for subject ID: {subject_id}")
        json_file_path = config.integrated_results_dir / f"{subject_id}_integrated_result.json"

        if not json_file_path.exists():
            raise HTTPException(
                status_code=404, 
                detail=f"Integrated result file not found for subject ID: {subject_id}"
            )

        try:
            integrated_result = json.loads(json_file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read or parse JSON for subject ID {subject_id}: {e}",
            )

        return {"status": "ok", "integrated_result": integrated_result}

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=7777, log_config=LOGGING_CONFIG)