#!/usr/bin/env python

import os
import json
import fcntl  # filecontrol
import time
import functools
import logging
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

from server import send_msg_to_discord


FILE_DIR = Path(__file__).resolve().parent
LOCK_FILE = FILE_DIR.parent / "logs" / "downloadTextReadingFiles.lock"
MIN_WEBM_FILES = 8
SILENCE_THRESHOLD = -40.0  # in dBFS
MIN_SILENCE_LEN = 150  # in milliseconds; the minimum duration of silence required before separating non-silent chunks.
MAX_HOURS_WAITED = 3


class Config:
    def __init__(self):
        self.source_dir = Path(__file__).resolve().parent
        self.experiment_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.experiment_id = os.getenv("EXPERIMENT_TEXTREADING_ID")
        self.exp_media_url = f"https://pavlovia.org/api/v2/experiments/{self.experiment_id}/media"
        self.gitlab_token = os.getenv("GITLAB_TOKEN")
        self.gitlab_header = {
            "oauthToken": self.gitlab_token
        }
        self.discord_webhook_url = os.getenv("DISCORD_BOT2_WEBHOOK_URL")
        self.discord_user_id = os.getenv("DISCORD_USER2_ID")
        self.data_dir = self.source_dir.parent / "data" / self.experiment_name
        self.subj_webm_ignored = self.data_dir / "subj_webm_ignored.txt"
        self.subj_webm_downloaded = self.data_dir / "subj_webm_downloaded.txt"
        self.subj_webm_good = self.data_dir / "subj_webm_good.txt"
        self.subj_webm_alerted = self.data_dir / "subj_webm_alerted.json"
        self.log_dir = self.source_dir.parent / "logs"
        self.log_fn_format = "downloadTextReadingFiles_%Y-%m-%d.log"


def setup_logger(config):
    logging.root.handlers = []
    logging.basicConfig(
        level=logging.INFO, 
        filename=config.log_dir / datetime.now().strftime(config.log_fn_format), 
        format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)4d: %(message)s"
    )
    logger = logging.getLogger(__name__)    
    return logger


def read_if_file_exist(filepath):
    if filepath.suffix == ".txt":
        try:
            return filepath.read_text().splitlines()
            # return pd.read_csv(filepath, header=None).values.flatten().tolist()
        except FileNotFoundError:
            return []

    elif filepath.suffix == ".json":
        try:
            return json.loads(filepath.read_text())
        except FileNotFoundError:
            return {}


def get_queue_map(config, logger):
    '''
    Glob all CSV files from the data path, 
    and return the IDs and filenames of participants 
    whose WEBM files were not marked as "downloaded" or "ignored".
    '''
    csv_files = list(config.data_dir.glob("*Z.csv"))  # raw experimental data
    subj_csv_map = { f.name.split("_")[0]: f.name for f in csv_files }
    subj_webm_downloaded = read_if_file_exist(config.subj_webm_downloaded)  # subjects marked as having downloaded their webm files
    subj_webm_ignored = read_if_file_exist(config.subj_webm_ignored)  # subjects marked as not needing to download their webm files
    download_queue = {}

    for subj in list(
        set(subj_csv_map.keys()) - set(subj_webm_downloaded) - set(subj_webm_ignored)
    ):
        webm_files = [
            fp for fp in config.data_dir.glob(f"{subj}_*.webm")  # raw audio data
            if "practice_loop" not in fp.name
        ]
        if len(webm_files) < MIN_WEBM_FILES:
            logger.info(f"Add {subj} to WEBM download queue.")
            download_queue[subj] = subj_csv_map[subj]
        else: 
            with open(config.subj_webm_downloaded, "a") as f:
                f.write(f"{subj}\n")

    return download_queue 


def get_csv_infos(download_queue, config, logger):
    csv_infos = {}

    for subj, csv_filename in list(download_queue.items()):
        df = pd.read_csv(config.data_dir / csv_filename)
        try:
            session_token = df["sessionToken"].values[0]
        except KeyError:  
            logger.warning(f"Column `sessionToken` not found in the CSV file of {subj}")
            download_queue.pop(subj)
            with open(config.subj_webm_ignored, "a") as f:
                f.write(f"{subj}\n")
        else:
            csv_infos[subj] = {
                "filename": csv_filename, 
                "sessionToken": session_token, 
                "date": df["date"].iloc[0],  # e.g., 2024-10-29_11h04.28.020
                "clips": df["mic_2.clip"].dropna().tolist()  # e.g., recording_mic_T05_2026-11-03_12h20.33.884
            }

    return csv_infos


def get_download_links(csv_infos, config, logger, timeout=60):
    res = requests.get(
        url=config.exp_media_url, 
        headers=config.gitlab_header, 
        timeout=timeout
    )
    if res.status_code != 200:
        logger.error(f"Failed to get media list: {res.status_code}")
        return None

    token_subj_map = { v["sessionToken"]: k for k, v in csv_infos.items() }

    return {
        upload["fileUrl"]: token_subj_map[upload["sessionToken"]]
        for upload in res.json()["uploads"]
        if upload["sessionToken"] in token_subj_map.keys()
    }


def download_medias(download_links, config, logger, timeout=120):
    subjs_downloaded = []

    for url, subj in download_links.items():
        # filename = os.path.basename(url)
        filename = subj + "_" + os.path.basename(url).split("_", 1)[-1]  # name after the SID of the csv file, which may be renamed by correcting_sid.sh
        filepath = config.data_dir / filename

        if filepath.exists():
            continue

        res = requests.get(
            url=url, 
            stream=True, 
            timeout=timeout
        )
        if res.status_code != 200:
            logger.error(f"Failed to download file with {url}. Status code: {res.status_code}")
            continue
        
        with open(filepath.with_suffix(filepath.suffix + ".part"), "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        filepath.with_suffix(filepath.suffix + ".part").rename(filepath)  # so that an interrupted download is not mistaken for a complete file

        logger.info(f"Downloaded: {url}")
        subjs_downloaded.append(filename.split("_")[0])

    return subjs_downloaded


def check_recordings(subj, csv_info, config, logger):
    date = csv_info["date"]
    filepaths = [
        (config.data_dir / f"{subj}_TextReading_{date}_{clip}.webm")
        for clip in csv_info["clips"]
    ]
    missing_clips, silent_clips = [], []

    for fp in filepaths:
        cid = fp.name.split("recording_mic_")[-1].split("_")[0]  # e.g., T05

        if not fp.exists():
            logger.warning(f"Clip {fp.name} not found for participant {subj}.")
            missing_clips.append(cid)
            continue

        try:
            audio = AudioSegment.from_file(fp)
            non_silent_ranges = detect_nonsilent(audio, min_silence_len=MIN_SILENCE_LEN, silence_thresh=SILENCE_THRESHOLD)
        except Exception as e:  # e.g., an empty or corrupted file
            logger.error(f"Failed to decode clip {fp.name} of participant {subj}: {e}")
            non_silent_ranges = []

        if len(non_silent_ranges) == 0:
            logger.warning(f"Clip {fp.name} of participant {subj} has no sound.")
            silent_clips.append(cid)

    return missing_clips, silent_clips


def update_is_file_ready(csv_filename, logger, timeout=30):
    url = f"https://qoca-api.chih-he.dev/tasks?csv_filename={csv_filename}"
    res = requests.get(
        url=url, 
        timeout=timeout
    )
    if res.status_code != 200:
        msg = f"{res.status_code} error, unable to send GET requests: {url}"
        logger.error(msg)
        return msg

    json_data = res.json()
    if len(json_data["items"]) == 0:
        msg = f"No task found for {csv_filename}"
        logger.error(msg)
        return msg

    status = json_data["items"][-1]["status"]
    if status == 1: 
        logger.info(f"Report is generated for {csv_filename}")
        return ""

    task_id = json_data["items"][-1]["id"]
    res = requests.put(
        url=f"https://qoca-api.chih-he.dev/tasks/{task_id}", 
        json={"is_file_ready": 1}
    )
    if res.status_code == 200:
        logger.info(f"Successfully updated `is_file_ready` for task #{task_id} ({csv_filename}) to 1.")
        return ""
    else:
        msg = f"Failed to update `is_file_ready` for task #{task_id} ({csv_filename}): {res.status_code}"
        logger.error(msg)
        return msg


def exclusive_run(lockfile):
    '''
    Using a single file as a lock, 
    a process must acquire the lock before it can read or write to a file. 
    Other processes that have not acquired the lock will be blocked from execution.

    Operations performs on fd (file descriptor):
    - LOCK_EX (Exclusive lock): Place an exclusive lock. Only one process may hold an exclusive lock for a given file at a given time.
    - LOCK_NB (Non-blocking): Make a nonblocking request. Directly raise an OSError instead of waiting if the lock cannot be acquired.    
    - LOCK_UN (Unlock): Remove an existing lock held by this process.

    Ref: https://docs.python.org/3/library/fcntl.html
    '''
    def decorator(func):

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            fd = open(lockfile, "w")
            locked = False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                return func(*args, **kwargs)

            except BlockingIOError: 
                print("Another instance is still running. Skip this run.")
                return None

            finally:
                if locked:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
                fd.close()

        return wrapper

    return decorator


@exclusive_run(LOCK_FILE)
def main():
    load_dotenv()
    config = Config()
    logger = setup_logger(config)

    download_queue = get_queue_map(config, logger)  # {subj: csv_filename}
    if len(download_queue) == 0:
        logger.info("Empty download queue.")
        return None

    logger.info(f"Start downloading WEBM files for {len(download_queue)} subjects ...")
    csv_infos = get_csv_infos(download_queue, config, logger)  # {subj: {filename, sessionToken, date, clips}}
    
    download_links = get_download_links(csv_infos, config, logger)  # {url: subj}
    if download_links is None:  # failed to get the media list
        return None

    subjs_downloaded = download_medias(download_links, config, logger)

    subjs_good = read_if_file_exist(config.subj_webm_good)
    subjs_alerted = read_if_file_exist(config.subj_webm_alerted)       

    for subj in sorted(set(subjs_downloaded)):
        missing_clips, silent_clips = check_recordings(subj, csv_infos[subj], config, logger)
        msg = ""

        if missing_clips or silent_clips:
            subjs_alerted[subj] = {"missing": missing_clips, "silent": silent_clips}
            msg += f"TextReading recordings of {subj} have abnormalities:"
            if missing_clips:
                msg += ("\nMissing clips:" + ", ".join(missing_clips))
            if silent_clips:
                msg += ("\nSilent clips:" + ", ".join(silent_clips))
        else:
            logger.info(f"All recordings of {subj} are complete and audible.")
            subjs_alerted.pop(subj, None)
            if subj not in subjs_good:
                subjs_good.append(subj)

        logger.info(f"Updating `is_file_ready` for {subj} ...")
        msg += update_is_file_ready(csv_infos[subj]["filename"], logger)

        if msg != "":
            send_msg_to_discord(msg, config)

    for subj, csv_info in csv_infos.items(): 
        csv_filepath = config.data_dir / csv_info["filename"]
        hours_waited = (time.time() - csv_filepath.stat().st_mtime) / (60 * 60)

        if (subj not in download_links.values()) and (subj not in subjs_alerted) and (hours_waited > MAX_HOURS_WAITED):
            msg = f"No recordings of {subj} found on Pavlovia {hours_waited:.0f} hours after its CSV file arrived."
            subjs_alerted[subj] = {"missing": "all"}
            send_msg_to_discord(msg, config)

    config.subj_webm_alerted.write_text(json.dumps(subjs_alerted))
    config.subj_webm_good.write_text("\n".join(subjs_good))
    

if __name__ == "__main__":
    main()
