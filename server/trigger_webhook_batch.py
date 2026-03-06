#!/usr/bin/env python

import os
import argparse
import subprocess
from datetime import datetime
import requests
from dotenv import load_dotenv

class Config:
    def __init__(self):
        self.source_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(self.source_dir, "..", "logs")
        self.log_file = os.path.join(self.log_dir, f"downloadData_{datetime.now().strftime('%Y-%m-%d')}.log")
        self.gitlab_commit_url = "https://gitlab.pavlovia.org/api/v4/projects/{}/repository/commits?ref_name=master&all=true{}"
        self.gitlab_token = os.getenv("GITLAB_TOKEN")
        self.gitlab_headers = {
            "Authorization": f"Bearer {self.gitlab_token}"
        }
        self.exp_gofitt_id        = os.getenv("EXPERIMENT_GOFITT_ID")
        self.exp_ospan_id         = os.getenv("EXPERIMENT_OSPAN_ID")
        self.exp_speechcomp_id    = os.getenv("EXPERIMENT_SPEECHCOMP_ID")
        self.exp_exclusion_id     = os.getenv("EXPERIMENT_EXCLUSION_ID")
        self.exp_textreading_id   = os.getenv("EXPERIMENT_TEXTREADING_ID")
        self.exp_id_list = [ self.exp_gofitt_id, self.exp_ospan_id, self.exp_speechcomp_id, self.exp_exclusion_id, self.exp_textreading_id ]

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-fd", "--from_date", type=str, default=None,
                        help="The start date for fetching commits (YYYY-MM-DD).")
    parser.add_argument("-td", "--to_date", type=str, default=None, 
                        help="The end date for fetching commits (YYYY-MM-DD).")
    parser.add_argument("-pp", "--per_page", type=int, default=None, 
                        help="Number of commits to fetch per page.")
    return parser.parse_args()

def get_commit_records(project_id, config, from_date, to_date, per_page):
    '''
    Fetch commit records for a given project within a specified date range.
    '''
    inqury = ""
    if from_date is not None:
        inqury += f"&since={from_date}T00:00:00Z"
    if to_date is not None:
        inqury += f"&until={to_date}T23:59:59Z"
    if per_page is not None:
        inqury += f"&per_page={per_page}"

    targ_url = config.gitlab_commit_url.format(project_id, inqury)
    print(f"Fetching commit records from:\n{targ_url}\n")
    resp = requests.get(url=targ_url, headers=config.gitlab_headers)

    if resp.status_code == 200:
        csv_names = []
        for commit in resp.json():
            commit_title = commit["title"].replace("data: ", "")
            if commit_title.endswith(".csv"):
                csv_name = commit_title.replace(":", "")
                csv_names.append(csv_name)
        return csv_names
    else:
        raise ValueError(f"Failed to fetch commit records for project {project_id}. Status code: {resp.status_code}")

if __name__ == "__main__":
    load_dotenv()
    config = Config()
    args = parse_args()
    log_msg = []

    try:
        for i, project_id in enumerate(config.exp_id_list):
            csv_names = get_commit_records(project_id, config, from_date=args.from_date, to_date=args.to_date, per_page=args.per_page)
            project_no = i + 1
            for csv_name in csv_names:
                cmd = ["python", "trigger_webhook.py", str(project_no), csv_name]
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                log_msg.append(" ".join(cmd) + "\n")
    finally:
        with open(config.log_file, "w") as f:
            f.writelines(log_msg)