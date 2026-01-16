#!/usr/bin/env python

import os
import glob
import logging
import requests
import pandas as pd
from dotenv import load_dotenv

class Config:
    def __init__(self):
        self.source_dir = os.path.dirname(os.path.abspath(__file__))
        self.experiment_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.experiment_id = os.getenv("EXPERIMENT_TEXTREADING_ID")
        self.exp_media_url = f"https://pavlovia.org/api/v2/experiments/{self.experiment_id}/media"
        self.gitlab_token = os.getenv("GITLAB_TOKEN")
        self.gitlab_header = {
            "oauthToken": self.gitlab_token
        }
        self.data_dir = os.path.join(self.source_dir, "..", "data", self.experiment_name)
        self.subj_webm_downloaded = os.path.join(self.data_dir, "subj_webm_downloaded.txt")
        self.subj_webm_ignored = os.path.join(self.data_dir, "subj_webm_ignored.txt")

def list_awaiting_files(config, logger):
    csv_files = glob.glob(os.path.join(config.data_dir, "*Z.csv")) # raw experimental data
    subj_list = [ os.path.basename(f).split("_")[0] for f in csv_files ]
    webm_files = glob.glob(os.path.join(config.data_dir, "*.webm")) # raw audio data
    subj_of_webm = [ os.path.basename(f).split("_")[0] for f in webm_files ]
    try:
        subj_webm_downloaded = ( # subjects marked as having downloaded their webm files
            pd.read_csv(config.subj_webm_downloaded, header=None)
            .values.flatten().tolist()
        ) 
    except:
        subj_webm_downloaded = []
    try:
        subj_webm_ignored = ( # subjects marked as not needing to download their webm files
            pd.read_csv(config.subj_webm_ignored, header=None)
            .values.flatten().tolist()
        ) 
    except:
        subj_webm_ignored = []

    subj_awaiting = list(set(subj_list) - set(subj_webm_downloaded) - set(subj_webm_ignored))
    not_ready_csv_filepaths = {}
    for subj in subj_awaiting:
        num_of_webm = subj_of_webm.count(subj) # a subject should have at least 8 webm files
        if num_of_webm < 8: 
            logger.info(f"Subject {subj} has {num_of_webm} webm files. Need at least 8.")
            not_ready_csv_filepaths[subj] = next(f for f in csv_files if os.path.basename(f).startswith(subj)) 
        else: 
            with open(config.subj_webm_downloaded, "a") as f:
                f.write(f"{subj}\n")

    return not_ready_csv_filepaths

def get_uploaded_not_downloaded(not_downloaded_tokens, config, logger):
    res = requests.get(
        url=config.exp_media_url, 
        headers=config.gitlab_header
    )
    if res.status_code == 200:
        json_data = res.json()
        uploads = json_data["uploads"]
        
        urls_to_download = []
        for upload in uploads:
            session_token = upload["sessionToken"]

            if session_token in not_downloaded_tokens:
                urls_to_download.append(upload["fileUrl"])

        return urls_to_download
    else:
        logger.error(f"Failed to get media list: {res.status_code}")
        return []

def update_is_file_ready(csv_filename, logger):
    res = requests.get(
        url=f"https://qoca-api.chih-he.dev/tasks?csv_filename={csv_filename}"
    )
    if res.status_code == 200:
        json_data = res.json()

        if len(json_data['items']) > 0:
            task_id = json_data['items'][-1]['id']
            status = json_data['items'][-1]['status']

            if status == 0: # report is not generated yet 
                res = requests.put(
                    url=f"https://qoca-api.chih-he.dev/tasks/{task_id}", 
                    json={
                        "is_file_ready": 1
                    }
                )
                if res.status_code == 200:
                    logger.info(f"Successfully updated is_file_ready of task #{task_id} ({csv_filename}) to 1.")
                else:
                    logger.error(f"Failed to update is_file_ready of task #{task_id} ({csv_filename}): {res.status_code}")
        else:
            logger.error(f"No task found for {csv_filename}")
    else:
        logger.error(f"Failed to assess report task status for {csv_filename}: {res.status_code}")

## ====================================================================================

if __name__ == "__main__":
    load_dotenv()
    config = Config()

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    not_ready_csv_filepaths = list_awaiting_files(config, logger)
    logger.info(f"Start downloading .webm files for {len(not_ready_csv_filepaths)} subjects ...")

    not_downloaded_tokens = []
    for subj, csv_filepath in not_ready_csv_filepaths:
        df = pd.read_csv(csv_filepath)
        try:
            session_token = df["sessionToken"].values[0]
            not_downloaded_tokens.append(session_token)
        except:
            logger.warning(f"Failed to get sessionToken for {subj}")
            not_ready_csv_filepaths.pop(subj)
            with open(config.subj_webm_ignored, "a") as f:
                f.write(f"{subj}\n")

    urls_to_download = get_uploaded_not_downloaded(not_downloaded_tokens, config, logger)

    if urls_to_download: # not empty list
        readied_csv_filenames = []

        for file_url in urls_to_download:
            res = requests.get(file_url, stream=True)

            if res.status_code == 200:
                file_name = os.path.basename(file_url)
                file_path = os.path.join(config.data_dir, file_name)
                with open(file_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info(f"Downloaded: {file_url}")

                subj = file_name.split("_")[0]
                if subj not in readied_csv_filenames:
                    csv_filename = os.path.basename(not_ready_csv_filepaths[subj])
                    update_is_file_ready(csv_filename, logger) 
                    readied_csv_filenames.append(subj)
            else:
                logger.info("Failed to download. Status code:", res.status_code)
    else:
        logger.info(f"No new files to download.")