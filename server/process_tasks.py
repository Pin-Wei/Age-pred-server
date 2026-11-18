#!/usr/bin/env python

import os
import logging
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from download_textreading_files import exclusive_run


FILE_DIR = Path(__file__).resolve().parent
LOCK_FILE = FILE_DIR.parent / "logs" / "processTasks.lock"


class Config:
    def __init__(self):
        self.source_dir = Path(__file__).resolve().parent
        self.log_dir = self.source_dir.parent / "logs"
        self.log_fn_format = "processTasks_%Y-%m-%d.log"
        self.process_textreading_url = os.getenv("PROCESS_TEXTREADING_URL")
        self.predict_url = os.getenv("PREDICT_URL")
        self.local_headers = {
            "X-GitLab-Token": "tcnl-project",
            "Content-Type": "application/json"
        }


def setup_logger(config):
    logging.root.handlers = []
    logging.basicConfig(
        level=logging.INFO, 
        filename=config.log_dir / datetime.now().strftime(config.log_fn_format), 
        format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)4d: %(message)s"
    )
    logger = logging.getLogger(__name__)    
    return logger


def execute_process_textreading(subject_id, csv_filename, config, logger, timeout=600): 
    res = requests.post(
        url=config.process_textreading_url, 
        headers=config.local_headers, 
        json={
            "subject_id": subject_id,
            "csv_filename": csv_filename
        }, 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to send process_textreading request for {subject_id}: {res.status_code}")
        return None

    result = res.json() or {}
    if not result.get("success"):
        logger.error(f"Failed to process text_reading files of {subject_id}: {result.get('message')}")
        return None

    logger.info(f"Successfully processed text_reading files of {subject_id} (MeanSR = {result.get('mean_speech_rate')})")
    return result


def get_user_info(subject_id, logger, timeout=30):
    res = requests.get(
        url=f"https://qoca-api.chih-he.dev/user/{subject_id}", 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to retrieve user info of {subject_id}: {res.status_code}")
        return None

    logger.info(f"Successfully retrieved user info of {subject_id}")
    return res.json()


def get_predict_result(age, subject_id, name, test_date, config, logger, timeout=30):
    res = requests.post(
        url=config.predict_url, 
        headers=config.local_headers, 
        json={
            "age": age, 
            "id_card": subject_id, 
            "name": name, 
            "test_date": test_date
        }, 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to retrieve predict_result for {subject_id}: {res.status_code}")
        return None

    logger.info(f"Successfully retrieved predict_result for {subject_id}")
    return res.json()


def update_predict_result(exam_id, predict_result, logger, timeout=30):
    '''
    The second report will be generate after 
    `report_status` in `predict_result` is initialized as `0`.
    '''
    res = requests.put(
        url=f"https://qoca-api.chih-he.dev/exams/{exam_id}", 
        json=predict_result, 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to update predict_result for exam #{exam_id}: {res.status_code}")
        return False
    
    logger.info(f"Successfully updated predict_result for exam #{exam_id}")
    return True


def update_report_status(task_id, status, logger, timeout=30):
    '''
    The first report will be generate after `status` is set to `1`.
    '''
    res = requests.put(
        url=f"https://qoca-api.chih-he.dev/tasks/{task_id}", 
        json={"status": status}, 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to update report status for task #{task_id}: {res.status_code}")
        return False
    
    logger.info(f"Successfully updated report status for task #{task_id} to {status}")
    return True


def process_task(task, config, logger):
    task_id = task["id"]
    exam_id = task["exam_id"]
    csv_filename = task["csv_filename"]
    subject_id = csv_filename.split("_")[0]
    test_date = Path(csv_filename).stem.split("_")[-1]

    process_result = execute_process_textreading(subject_id, csv_filename, config, logger)
    if process_result is None:
        return

    user_info = get_user_info(subject_id, logger)
    if user_info is None:
        return

    predict_result = get_predict_result(user_info["age"], subject_id, user_info["name"], test_date, config, logger)
    if predict_result is None:
        return

    predict_result["report_status"] = 0
    predict_result["testDate"] = datetime.strptime(predict_result["testDate"], "%Y-%m-%dT%H%M%S.%fZ").isoformat()
    
    exam_is_updated = update_predict_result(exam_id, predict_result, logger)
    if exam_is_updated:  # any failure above leaves `status=0`, so the task will be retried in the next run
        task_is_updated = update_report_status(task_id, 1, logger)
        

@exclusive_run(LOCK_FILE)
def main():
    load_dotenv()
    config = Config()
    logger = setup_logger(config)
    
    ## Search for tasks that need to be processed (is_file_ready=1 & status=0)
    url = "https://qoca-api.chih-he.dev/tasks?is_file_ready=1&status=0"
    res = requests.get(
        url=url, 
        timeout=30
    )
    if res.status_code != 200:
        logger.error(f"{res.status_code} error, unable to send GET requests: {url}")
        return

    json_data = res.json()
    tasks = json_data["items"]

    if len(tasks) == 0:
        logger.info("No tasks to process")
    else:
        logger.info(f"Retrieved {len(tasks)} tasks to process")

        for task in tasks:
            try:
                process_task(task, config, logger)
            except Exception as e:
                logger.exception(f"Unexpected error while processing task #{task['id']}: {e}")


if __name__ == "__main__":
    main()