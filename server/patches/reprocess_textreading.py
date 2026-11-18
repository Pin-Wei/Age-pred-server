#!/usr/bin/env python

# This script is used to re-process text reading files and was called by the tidy_predicted_results.py
# Usage: python patches/reprocess_textreading.py <subject_id>

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

FILE_DIR = Path(__file__).resolve().parent
MAIN_DIR = FILE_DIR.parent

sys.path.append(str(MAIN_DIR))
import util
from server import setup_logger, predict, upload_exam
from download_textreading_files import update_is_file_ready
from process_tasks import execute_process_textreading


class Config:
    def __init__(self):
        self.data_dir = MAIN_DIR.parent / "data"
        self.integrated_results_dir = MAIN_DIR / "integrated_results"
        self.exp_textreading_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.process_textreading_url = os.getenv("PROCESS_TEXTREADING_URL")
        self.predict_url = os.getenv("PREDICT_URL")
        self.local_headers = {
            "X-GitLab-Token": "tcnl-project",
            "Content-Type": "application/json"
        }
        self.qoca_url = (os.getenv("QOCA_URL") or "https://qoca-api.chih-he.dev").rstrip("/")
        self.qoca_headers = {
            "Content-Type": "application/json"
        }
        self.platform_features = util.init_platform_features()
        self.missing_marker = -999


def get_aud_csv_files(subject_id, config):
    aud_csv_files = list(
        (config.data_dir / config.exp_textreading_name).glob(f"{subject_id}_*_ds.wav.words.csv")
    )
    aud_csv_files = [ f for f in aud_csv_files if "practice_loop" not in f.name ]  # exclude practice files
    return aud_csv_files


if __name__ == "__main__":
    load_dotenv(dotenv_path=MAIN_DIR / ".env")
    config = Config()
    logger = setup_logger()

    subject_id = sys.argv[1]
    print(f"\n---  {subject_id} ---\n")

    ## Check if text_reading files exist:
    main_csv_files = list((config.data_dir / config.exp_textreading_name).glob(f"{subject_id}_*Z.csv"))
    if not main_csv_files:
        logger.warning("No text_reading files found. Goodbye :-O")
        sys.exit(1)

    ## In case is_file_ready has not been marked as 1:
    csv_filename = sorted(main_csv_files, key=lambda f: f.stem.split("_")[-1])[-1].name  # the latest one
    update_is_file_ready(csv_filename, logger)

    ## Process text_reading files if needed:
    aud_csv_files = get_aud_csv_files(subject_id, config)

    if not aud_csv_files:
        logger.info("Processing text_reading files ...")
        execute_process_textreading(subject_id, csv_filename, config, logger)
        aud_csv_files = get_aud_csv_files(subject_id, config)
    
    if not aud_csv_files: # should not happen
        logger.warning("Something went wrong." + 
                       "\nYou may check whether the audio files have been downloaded for the participant." + 
                       "\nGoodbye :-(")
        sys.exit(1)

    ## Re-generate predict result:
    test_date = Path(csv_filename).stem.split("_")[-1]
    predict_result = predict(subject_id, config, test_date=test_date)

    if predict_result is not None:
        exam_id = upload_exam(predict_result, config)
        logger.info("Goodbye :-)")
    else:
        logger.warning("Failed to produce predict_result. Goodbye :-(")

