#!/usr/bin/env python

import os
import sys
import glob

import numpy as np
import pandas as pd
from dotenv import load_dotenv

import util
from server import update_json_result, setup_logger
from download_textreading_files import update_is_file_ready
from data_processors.textreading_processor import TextReadingProcessor

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
        self.platform_features = util.init_platform_features()
        self.missing_marker = -999

if __name__ == "__main__":
    load_dotenv()
    config = Config()
    logger = setup_logger()
    text_reading_processor = TextReadingProcessor(
        data_dir=os.path.join(config.data_dir, config.exp_textreading_name)
    )
    subject_id = sys.argv[1]

    ## In case is_file_ready has not been marked as 1:
    main_csv_files = glob.glob(os.path.join(config.data_dir, config.exp_textreading_name, f"{subject_id}_*Z.csv"))
    
    if not main_csv_files:
        print(f"No text_reading files for subject {subject_id}")
        sys.exit(1)

    csv_filename = os.path.basename(main_csv_files[0])
    update_is_file_ready(csv_filename, logger)

    ## Calculate mean speech rate:
    aud_csv_files = list(
        glob.glob(os.path.join(config.data_dir, config.exp_textreading_name, f"{subject_id}_*_ds.wav.words.csv"))
    )
    aud_csv_files = [ f for f in aud_csv_files if "practice_loop" not in f ]  # exclude practice files

    if aud_csv_files:
        mean_speech_rate = text_reading_processor.calculate_mean_syllable_speech_rate(aud_csv_files)

        if pd.isna(mean_speech_rate) or mean_speech_rate == float('inf'):
            print(f"No valid speech rate calculated for subject {subject_id}")
        else:
            result_df = pd.DataFrame({
                'ID': [subject_id],
                'LANGUAGE_READING_BEH_NULL_MeanSR': [mean_speech_rate]
            })
            update_json_result(subject_id, result_df, config, logger)
            print(f"Mean speech rate for subject {subject_id} is {mean_speech_rate}")
    else:
        print(f"No valid *_ds.wav.words.csv files for subject {subject_id}")
