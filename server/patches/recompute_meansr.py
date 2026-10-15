#!/usr/bin/env python

# This script is used to re-compute LANGUAGE_READING_BEH_NULL_MeanSR 
# based on the CSV files generated from audio files by Whisper
# for every subject in `tidy_predicted_results.csv`
# and update both `<subject_id>_integrated_result.json` and `tidy_predicted_results.csv`.
#
# Usage: python recompute_meansr.py

import os
import sys
import glob

import pandas as pd
from dotenv import load_dotenv

FILE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_DIR = os.path.abspath(os.path.join(FILE_DIR, "..")) 

sys.path.append(MAIN_DIR)
import util
from data_processors.textreading_processor import TextReadingProcessor
from server import setup_logger, update_json_result


class Config:
    def __init__(self):
        self.exp_textreading_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.data_dir = os.path.join(MAIN_DIR, "..", "data", self.exp_textreading_name)
        self.integrated_results_dir = os.path.join(MAIN_DIR, "integrated_results")
        self.tidy_predicted_results = os.path.join(MAIN_DIR, "predicted_results", "tidy_predicted_results.csv")
        self.col_name = "LANGUAGE_READING_BEH_NULL_MeanSR"
        self.missing_marker = -999
        self.platform_features = util.init_platform_features()


def main():
    config = Config()
    logger = setup_logger()
    processor = TextReadingProcessor(data_dir=config.data_dir)
    df = pd.read_csv(config.tidy_predicted_results, encoding="utf-8-sig")

    new_scores = []
    for sid, session in zip(df["SID"], df["Session"]):
        subject_id = f"{sid}-{session}"
        aud_csv_files = [ 
            f for f in glob.glob(
                os.path.join(config.data_dir, f"{subject_id}_*_ds.wav.words.csv")
            ) if "practice_loop" not in f
        ]

        # sessions = sorted({ os.path.basename(f).split("_recording_mic_")[0] for f in aud_csv_files })
        # if len(sessions) > 1:
        #     print(f"{subject_id}: {len(sessions)} test dates found")
        #     exit()

        if not aud_csv_files:
            print(f"No audio-generated CSV file found for {subject_id}.")
            new_scores.append(pd.NA)
        else:
            mean_speech_rate = processor.calculate_mean_syllable_speech_rate(aud_csv_files)
            new_scores.append(mean_speech_rate)

            result_df = pd.DataFrame({
                "ID": [subject_id],
                config.col_name: [mean_speech_rate]
            })
            update_json_result(subject_id, result_df, config, logger)

    df[config.col_name] = new_scores
    df.to_csv(config.tidy_predicted_results, index=False, encoding="utf-8-sig")

    n_done = df[config.col_name].notna().sum()
    print(f"\nRecomputed {config.col_name} for {n_done} of {len(df)} subjects")


if __name__ == "__main__":
    load_dotenv(dotenv_path=os.path.join(MAIN_DIR, ".env"))
    main()