#!/usr/bin/env python

# This script is used to re-process text reading files and was called by the tidy_predicted_results.py
# Usage: python patches.py <subject_id>

import os
import sys
import glob

import numpy as np
import pandas as pd
from dotenv import load_dotenv

import util
from server import update_json_result, setup_logger, predict, upload_exam
from download_textreading_files import update_is_file_ready
from process_tasks import execute_process_textreading
# from data_processors.textreading_processor import TextReadingProcessor

class Config:
    def __init__(self):
        self.source_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.source_dir, "..", "data")
        self.integrated_results_dir = os.path.join(self.source_dir, "integrated_results")
        self.exp_textreading_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.process_textreading_url = os.getenv("PROCESS_TEXTREADING_URL")
        self.predict_url = os.getenv("PREDICT_URL")
        self.local_headers = {
            "X-GitLab-Token": "tcnl-project",
            "Content-Type": "application/json"
        }
        self.qoca_headers = {
            "Content-Type": "application/json"
        }
        self.platform_features = util.init_platform_features()
        self.missing_marker = -999

def get_aud_csv_files(subject_id, config):
    aud_csv_files = list(
        glob.glob(os.path.join(config.data_dir, config.exp_textreading_name, f"{subject_id}_*_ds.wav.words.csv"))
    )
    aud_csv_files = [ f for f in aud_csv_files if "practice_loop" not in f ]  # exclude practice files
    return aud_csv_files

if __name__ == "__main__":
    load_dotenv()
    config = Config()
    logger = setup_logger()

    subject_id = sys.argv[1]
    print(f"\n---  {subject_id} ---\n")

    ## Check if text_reading files exist:
    main_csv_files = glob.glob(os.path.join(config.data_dir, config.exp_textreading_name, f"{subject_id}_*Z.csv"))
    if not main_csv_files:
        logger.warning("No text_reading files found. Goodbye :-O")
        sys.exit(1)

    ## In case is_file_ready has not been marked as 1:
    csv_filename = os.path.basename(main_csv_files[0])
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

    # ## Calculate mean speech rate:
    # text_reading_processor = TextReadingProcessor(
    #     data_dir=os.path.join(config.data_dir, config.exp_textreading_name)
    # )
    # mean_speech_rate = text_reading_processor.calculate_mean_syllable_speech_rate(aud_csv_files)

    # if pd.isna(mean_speech_rate) or mean_speech_rate == float('inf'):
    #     logger.warning("No valid speech rate calculated. Goodbye :-(")
    #     sys.exit(1)
    # else:
    #     logger.info(f"Mean speech rate is {mean_speech_rate}")
    #     result_df = pd.DataFrame({
    #         'ID': [subject_id],
    #         'LANGUAGE_READING_BEH_NULL_MeanSR': [mean_speech_rate]
    #     })
    #     update_json_result(subject_id, result_df, config, logger)

    ## Re-generate predict result:
    predict_result = predict(subject_id, config, logger)
    if predict_result is not None:
        exam_id = upload_exam(predict_result, config, logger)
        logger.info("Goodbye :-)")
    else:
        logger.warning("Failed to produce predict_result. Goodbye :-(")

