#!/usr/bin/env python

# This script is used to manually update the is_file_ready field for a subject to 1.
# Usage: python mannual_mark_ready.py <subject_id>

import os
import sys
import glob
from dotenv import load_dotenv
from server import setup_logger
from download_textReading_files import update_is_file_ready

class Config:
    def __init__(self):
        self.source_dir = os.path.dirname(os.path.abspath(__file__))
        self.experiment_name = os.getenv("EXPERIMENT_TEXTREADING_NAME")
        self.data_dir = os.path.join(self.source_dir, "..", "data", self.experiment_name)

if __name__ == "__main__":
    load_dotenv()
    config = Config()
    logger = setup_logger()

    subj = sys.argv[1]
    csv_filepath = glob.glob(os.path.join(config.data_dir, f"{subj}_*Z.csv"))[0]
    csv_filename = os.path.basename(csv_filepath)
    update_is_file_ready(csv_filename, logger)