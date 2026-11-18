#!/usr/bin/env python

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from pydantic import BaseModel

import util
from uvicorn_config import LOGGING_CONFIG
from task_integrator import TaskIntegrator, process_and_format_result


MISSING_MARKER = -999
logger = logging.getLogger(__name__)


class Config:
    def __init__(self):
        self._setup_paths()
        self._setup_auths()
        self._setup_vars()

    def _setup_paths(self):
        self.source_dir = Path(__file__).resolve().parent
        self.data_dir = self.source_dir.parent / "data"
        self.integrated_results_dir = self.source_dir / "integrated_results"
        self.fetch_file_url = "https://gitlab.pavlovia.org/api/v4/projects/{}/repository/files/data%2F{}/raw?ref=master"
        self.discord_webhook_url = os.getenv("DISCORD_BOT1_WEBHOOK_URL")
        self.predict_url = os.getenv("PREDICT_URL")
        self.qoca_url = (os.getenv("QOCA_URL") or "https://qoca-api.chih-he.dev").rstrip("/")

    def _setup_auths(self):
        self.x_gitlab_token = os.getenv("X_GITLAB_TOKEN", "tcnl-project")
        self.local_headers = {
            "X-GitLab-Token": self.x_gitlab_token,
            "Content-Type": "application/json"
        }
        self.gitlab_token = os.getenv("GITLAB_TOKEN")
        self.gitlab_headers = {
            "Authorization": f"Bearer {self.gitlab_token}"
        }
        self.qoca_headers = {
            "Content-Type": "application/json"
        }

    def _setup_vars(self):
        self.exp_gofitt_name = os.getenv("EXPERIMENT_GOFITT_NAME")
        self.exp_ospan_name = os.getenv("EXPERIMENT_OSPAN_NAME")
        self.exp_speechcomp_name = os.getenv("EXPERIMENT_SPEECHCOMP_NAME")
        self.exp_exclusion_name = os.getenv("EXPERIMENT_EXCLUSION_NAME")
        self.exp_textreading_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.processable_exps = [
            self.exp_gofitt_name,
            self.exp_ospan_name,
            self.exp_speechcomp_name,
            self.exp_exclusion_name,
        ]
        self.exp_name_list = self.processable_exps + [ self.exp_textreading_name ]
        self.platform_features = util.init_platform_features()
        self.discord_user_id = int(os.getenv("DISCORD_USER1_ID"))


class ReportRequest(BaseModel):
    subject_id: str


class WebhookPayload(BaseModel):
    project: Dict[str, Any]
    commits: List[Dict[str, Any]] = []


@lru_cache()
def get_config() -> Config:
    load_dotenv()
    return Config()


def setup_logger(name: str = __name__) -> logging.Logger:
    target_logger = logging.getLogger(name)
    target_logger.handlers.clear()
    target_logger.setLevel(logging.INFO)
    target_logger.propagate = False
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    target_logger.addHandler(handler)

    return target_logger


def authenticate_gitlab(
    x_gitlab_token: str = Header(...),
    config: Config = Depends(get_config),
):
    if x_gitlab_token != config.x_gitlab_token:
        raise HTTPException(status_code=403, detail="Invalid X-GitLab-Token")
    return x_gitlab_token


def send_msg_to_discord(msg: str, config: Config, timeout: int = 30):
    if not config.discord_webhook_url:
        logger.warning("Discord webhook URL is not configured.")
        return {"status": "skipped"}

    json_data = {
        "content": f"<@{config.discord_user_id}> {msg}",
        "allowed_mentions": {"users": [config.discord_user_id]}
    }
    try:
        res = requests.post(
            url=config.discord_webhook_url, 
            json=json_data, 
            timeout=timeout
        )
        if res.status_code != 204:
            logger.error(f"Failed to send message to Discord. Status: {res.status_code}, Body: {res.text}")
            return {"status": "failed", "code": res.status_code}

        logger.info("Successfully sent message to Discord")
        return {"status": "ok"}

    except requests.RequestException as e:
        logger.error(f"Discord request exception: {e}")
        return {"status": "failed", "error": str(e)}


def fetch_file(project_name: str, project_id: int, filename: str, config: Config, timeout: int = 60) -> Optional[Path]:
    project_dir = config.data_dir / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    file_path = project_dir / filename
    
    if file_path.exists():
        logger.info(f"File {file_path} already exists, skipping download.")
        return file_path

    logger.info(f"Fetching file {filename} from project {project_name}.")
    try:
        resp = requests.get(
            url=config.fetch_file_url.format(project_id, filename), 
            headers=config.gitlab_headers, 
            timeout=timeout
        )
    except requests.RequestException as e:
        logger.error(f"Network error while fetching {filename} from {project_name}: {e}")
        return None

    if resp.status_code != 200:
        logger.error(
            f"Failed to fetch file {filename} from project {project_name}. "
            f"Status: {resp.status_code}, Response: {resp.text}"
        )
        return None

    file_path.write_bytes(resp.content)
    logger.info(f"Successfully fetched file {filename} from project {project_name}.")

    return file_path


def convert_np_types(obj):
    if isinstance(obj, (np.integer, np.int64)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist() # Convert np.ndarray to list
    elif isinstance(obj, np.generic): 
        return obj.item() # Convert np.generic to scalar
    elif isinstance(obj, list):
        return [ convert_np_types(i) for i in obj ] 
    elif isinstance(obj, dict):
        return { k: convert_np_types(v) for k, v in obj.items() } 
    else:
        return obj


def update_json_result(subject_id: str, result_df: pd.DataFrame, config: Config):
    config.integrated_results_dir.mkdir(parents=True, exist_ok=True)
    json_file_path = config.integrated_results_dir / f"{subject_id}_integrated_result.json"
    try:
        existing_data = json.loads(json_file_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        existing_data = { 
            feature: MISSING_MARKER for feature in config.platform_features 
        }

    result_df = result_df.replace([pd.NA, pd.NaT, float("inf"), float("-inf")], MISSING_MARKER)
    result_df = result_df.fillna(MISSING_MARKER)
    formatted_result = process_and_format_result(result_df, config.platform_features)
    
    for key, value in formatted_result.items():
        if value != MISSING_MARKER:
            existing_data[key] = value
        elif key not in existing_data:
            existing_data[key] = MISSING_MARKER

    with open(json_file_path, "w") as f:
        json.dump(existing_data, f, indent=2, default=convert_np_types)

    logger.info(f"Successfully updated {json_file_path}")


def process_file(project_name: str, filepath: Path, config: Config):
    subject_id = Path(filepath).name.split("_")[0]
    result_df = None
    
    if project_name in config.processable_exps:
        result_df = TaskIntegrator().process_subject(subject_id, tasks_to_process=[project_name])
    elif project_name == config.exp_textreading_name:
        logger.info("Skipping data processing for TextReading")
        return
    else:
        logger.warning(f"Unrecognized project name: {project_name}")
        return

    if result_df is not None:
        logger.info(f"Successfully processed data from {project_name}")
        update_json_result(subject_id, result_df, config)
    else:
        logger.warning(f"No results found for {project_name} (subject: {subject_id})")


def predict(id_card: str, config: Config, test_date: Optional[str] = None, timeout: int = 30) -> Optional[Dict[str, Any]]:
    try:
        res = requests.get(
            url=f"{config.qoca_url}/user/{id_card}",
            timeout=timeout,
        )
    except requests.RequestException as e:
        logger.error(f"Request error when retrieving user info of {id_card}: {e}")
        return None

    if res.status_code != 200:
        logger.error(f"Failed to retrieve user info of {id_card}. Status code: {res.status_code}")
        return None
        
    user_info = res.json()
    logger.info(f"Successfully retrieved user info for {id_card}")

    if test_date is None:
        now = datetime.now(timezone.utc)
        test_date = now.strftime("%Y-%m-%dT%H%M%S.") + f"{int(now.microsecond / 1000):03d}Z"

    try:
        res = requests.post(
            url=config.predict_url, 
            headers=config.local_headers, 
            json={
                "age": user_info["age"],
                "id_card": id_card,
                "name": user_info["name"],
                "test_date": test_date
            }
        )
    except requests.RequestException as e:
        logger.error(f"Request error when retrieving prediction result of {id_card}: {e}")
        return None
        
    if res.status_code != 200:
        logger.error(f"Failed to retrieve prediction result of {id_card}. Status code: {res.status_code}")
        return None

    logger.info(f"Successfully retrieved prediction result of {id_card}")
    return res.json()


def parse_iso_date(s: str) -> str:
    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ", 
        "%Y-%m-%dT%H%M%S.%fZ"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unknown date format: {s!r}")        


def upload_exam(exam: Dict[str, Any], config: Config, timeout: int = 30) -> Optional[str]:
    try:
        exam["testDate"] = parse_iso_date(exam["testDate"])
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid testDate in exam payload: {e}")
        return None

    try:
        res = requests.post(
            url="https://qoca-api.chih-he.dev/exams", 
            headers=config.qoca_headers, 
            json=exam, 
            timeout=timeout
        )
    except requests.RequestException as e:
        logger.error(f"Request error when uploading exam: {e}")
        return None

    if res.status_code != 201:
        logger.error(f"Failed to upload predict_result. Status: {res.status_code}, Body: {res.text}")
        return None

    exam_id = res.json().get("id")
    logger.info(f"Successfully uploaded predict_result (exam_id={exam_id})")
    
    return exam_id


def create_task(exam_id: str, csv_filename: str, config: Config, timeout: int = 30) -> bool:
    try:
        res = requests.post(
            url=f"{config.qoca_url}/tasks", 
            headers=config.qoca_headers, 
            json={
                "exam_id": exam_id, 
                "csv_filename": csv_filename
            }, 
            timeout=timeout
        )
    except requests.RequestException as e:
        logger.error(f"Request error when creating task (exam_id={exam_id}): {e}")
        return False

    if res.status_code != 201:
        logger.error(f"Failed to create report-generating task (exam_id={exam_id}). Status: {res.status_code}, Body: {res.text}")
        return False
    
    logger.info(f"Successfully created report-generating task (exam_id={exam_id})")
    return True


def handle_single_commit_file(project_name: str, project_id: int, filename: str, commiter: str, config: Config):
    subject_id = filename.split("_")[0]

    # if commiter == "Pavlovia Committer":
    #     msg = f"Data from {project_name} has been uploaded for subject {subject_id}"
    #     send_msg_to_discord(msg, config)

    filepath = fetch_file(project_name, project_id, filename, config)    
    if not filepath:
        return

    process_file(project_name, filepath, config)

    if project_name == config.exp_textreading_name:
        test_date = Path(filename).stem.split("_")[-1]  # e.g., 2026-11-03T020844.505Z
        predict_result = predict(subject_id, config, test_date=test_date)
        if not predict_result:
            return

        exam_id = upload_exam(predict_result, config)
        if not exam_id:
            return

        create_task(exam_id, filename, config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    logger.info("Server initialized and directories ensured.")
    yield


def register_exception_handlers(app: FastAPI, service_logger: logging.Logger):
    @app.exception_handler(HTTPException)
    async def custom_http_exception_handler(request: Request, exc: HTTPException):
        service_logger.error(f"HTTP {exc.status_code} on {request.method} {request.url.path}: {exc.detail}")
        return await http_exception_handler(request, exc)


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, lifespan=lifespan)
    register_exception_handlers(app, logger)

    @app.get("/")
    def read_root():
        logger.info("Hello World")
        return {"status": "ok"}

    @app.post("/webhook")
    def receive_webhook(
        payload: WebhookPayload,
        background_tasks: BackgroundTasks,
        token: str = Depends(authenticate_gitlab),
        config: Config = Depends(get_config),
    ):
        project_name = payload.project.get("name")
        project_id = payload.project.get("id")
        commits = payload.commits        

        logger.info(f"Received {len(commits)} commits from project {project_name} ({project_id})")
        
        queued_files = []
        ignored_reasons = []

        for commit in commits:
            commiter = commit.get("author", {}).get("name", "")
            if commiter not in ["Pavlovia Committer", "Local Committer"]:
                logger.warning(f"Ignoring commit from non-Pavlovia Committer: {commiter}")
                ignored_reasons.append("non-Pavlovia Committer")
                continue
            
            added_files = commit.get("added", [])
            if not added_files:
                logger.warning("Ignoring commit with no added files.")
                ignored_reasons.append("no added files")
                continue

            for raw_filepath in added_files:
                filename = PurePosixPath(raw_filepath).name

                if not filename.lower().endswith(".csv"):
                    logger.warning(f"Ignoring non-CSV file: {filename}")
                    ignored_reasons.append(f"non-CSV file ({filename})")
                    continue

                background_tasks.add_task(
                    handle_single_commit_file,
                    project_name=project_name,
                    project_id=project_id,
                    filename=filename,
                    commiter=commiter,
                    config=config,
                )
                queued_files.append(filename)
                
        if not queued_files:
            return {"status": "ignored", "reasons": ignored_reasons}

        return {"status": "ok", "queued_files": queued_files}

    @app.post("/report")
    def create_report(
        body: ReportRequest,
        config: Config = Depends(get_config),
    ):
        predict_result = predict(body.subject_id, config)
        if not predict_result:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to produce predict_result for subject {body.subject_id}",
            )

        exam_id = upload_exam(predict_result, config)
        if not exam_id:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to upload exam result for subject {body.subject_id}",
            )

        return {"status": "ok", "exam_id": exam_id}

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=LOGGING_CONFIG)
    