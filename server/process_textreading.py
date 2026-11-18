#!/usr/bin/env python

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

import pandas as pd
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from data_processors.textreading_processor import TextReadingProcessor
from server import (
    MISSING_MARKER, 
    Config, 
    get_config, 
    setup_logger, 
    register_exception_handlers, 
    authenticate_gitlab, 
    update_json_result
)
from uvicorn_config import LOGGING_CONFIG


logger = logging.getLogger(__name__)


class SubjectReprocessRequest(BaseModel):
    subject_id: str
    csv_filename: str 


def process_text_reading(subject_id: str, csv_filename: str, config: Config) -> Dict[str, Any]:
    textreading_dir = config.data_dir / config.exp_textreading_name
    text_reading_processor = TextReadingProcessor(data_dir=textreading_dir)
    result = {
        "status": "error",
        "message": "",
        "files_processed": [],
        "mean_speech_rate": None,
        "success": False,
        "csv_filename": csv_filename
    }    
    csv_path = textreading_dir / csv_filename
    
    if not csv_path.exists():
        result["message"] = f"CSV file not found: {csv_filename}"
        result["error_code"] = 404
        return result

    try:
        df = pd.read_csv(csv_path)
        if df.empty or "date" not in df.columns:
            result["message"] = f"CSV file {csv_filename} is empty or missing 'date' column"
            result["error_code"] = 422
            return result

        test_date = df['date'].iloc[0]  # e.g., 2024-10-29_11h04.28.020
        logger.info(f"Test date from CSV: {test_date}")
        
        pattern = f"{subject_id}_TextReading_{test_date}_recording_mic_*.webm"
        webm_files = [ 
            f for f in textreading_dir.glob(pattern) 
            if "practice_loop" not in f.name  # exclude practice files
        ]

        if not webm_files:
            result["message"] = f"No audio files found for subject {subject_id} on date {test_date}"
            result["error_code"] = 404
            return result

        logger.info(f"Found {len(webm_files)} audio files for subject {subject_id} on date {test_date}")
        result["files_processed"] = [ f.name for f in webm_files ]
        csv_files = []

        for webm_file in webm_files:
            logger.info(f"\nProcessing audio file: {webm_file.name}")
            try:
                csv_file = text_reading_processor.generate_csv(webm_file)
                if csv_file:
                    csv_files.append(csv_file)
            except Exception as e:
                logger.error(f"\nFailed to process file {webm_file.name}: {str(e)}")

        if len(csv_files) == 0:
            result["message"] = f"No CSV files generated for subject {subject_id}"
            result["error_code"] = 422
            return result

        mean_speech_rate = text_reading_processor.calculate_mean_syllable_speech_rate(csv_files)

        if mean_speech_rate is None:
            result["message"] = f"Failed to calculate mean speech rate for subject {subject_id}."
            result["error_code"] = 422
            return result

        if pd.isna(mean_speech_rate) or mean_speech_rate in (float("inf"), float("-inf")):
            result["message"] = f"Invalid mean speech rate for subject {subject_id}: {mean_speech_rate}"
            result["error_code"] = 422
            return result

        logger.info(f"\nMean speech rate for subject {subject_id}: {mean_speech_rate}")
        result_df = pd.DataFrame({
            'ID': [subject_id],
            'LANGUAGE_READING_BEH_NULL_MeanSR': [mean_speech_rate]
        })
        update_json_result(subject_id, result_df, config)
        logger.info(f"\nDone processing for subject {subject_id}!\n")
        
        result.update({
            "status": "success",
            "message": "Successfully processed audio files and calculated mean speech rate.",
            "mean_speech_rate": mean_speech_rate,
            "success": True
        })
        return result

    except Exception as e:
        result["message"] = f"Error processing text reading for {subject_id}: {e}"
        result["error_code"] = 500
        return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(__name__)
    cfg = get_config()
    logger.info("process_textreading service initialized.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, lifespan=lifespan)
    register_exception_handlers(app, logger)

    @app.post("/process_textreading")
    def reprocess_subject(
        request: SubjectReprocessRequest,
        token: str = Depends(authenticate_gitlab),
        config: Config = Depends(get_config),
    ):
        logger.info(
            f"Starting to process CSV file for subject ID: {request.subject_id} "
            f"with filename: {request.csv_filename}"
        )
        result = process_text_reading(request.subject_id, request.csv_filename, config)

        if not result["success"]:
            status_code = result.pop("error_code", 422)
            raise HTTPException(status_code=status_code, detail=result["message"])

        return result

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=6666, log_config=LOGGING_CONFIG)