#!/usr/bin/env python

# The purpose of this script is to generate age predictions using data from a CSV file.
# To be more specific, this script reads the ~400 participants data matrix, predict their brain age, and save the results to a CSV table.

import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone
from dotenv import load_dotenv
import util

class Config:
    def __init__(self):
        self.source_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_path = os.path.join(self.source_dir, "..", "data", "DATA_ses-01_2024-12-05.csv")
        self.json_path = os.path.join(self.source_dir, "integrated_results", "{}_integrated_result.json")
        self.table_path = os.path.join(self.source_dir, "predicted_results", "background_predicted_results.csv")
        self.predict_url = os.getenv("PREDICT_URL") 
        self.local_headers = {
            "X-GitLab-Token": "tcnl-project",
            "Content-Type": "application/json"
        }

def pseudo_predict(config, sid, age):
    now = datetime.now(timezone.utc)
    res = requests.post(
        url=config.predict_url, 
        headers=config.local_headers, 
        json={
            "age": age,
            "id_card": sid,
            "name": sid,
            "test_date": now.strftime('%Y-%m-%dT%H%M%S.') + f"{int(now.microsecond / 1000):03d}Z"
        }
    )
    if res.status_code == 200:
        return res.json()
    else:
        raise Exception(f"Error {res.status_code}: {res.text}")

if __name__ == "__main__":
    load_dotenv()
    config = Config()

    data = pd.read_csv(config.data_path, encoding='utf-8')
    print(f"Data loaded: {config.data_path}")
    
    platform_features = util.init_platform_features()
    data = data.loc[:, ["BASIC_INFO_ID", "BASIC_INFO_AGE"] + platform_features]
    data = data.fillna(-999)

    out_rows = []
    for idx, row in data.iterrows():
        sid = row["BASIC_INFO_ID"]
        age = row["BASIC_INFO_AGE"]
        features = row[platform_features].to_dict()

        with open(config.json_path.format(sid), "w", encoding="utf-8") as f:
            json.dump(features, f, indent=2)

        print(F"JSON file saved: {sid}_integrated_result.json")

        predict_out = pseudo_predict(config, sid, age)
        formatted_out = {
            "SID": predict_out["id_card"], 
            "Chronological Age": predict_out["results"]["chronologicalAge"],
            "Brain Age": predict_out["results"]["brainAge"],
            "PAD": predict_out["results"]["originalPAD"],
            "Corrected PAD": predict_out["results"]["ageCorrectedPAD"]
        }
        formatted_out.update({ 
            item["name"]: item["score"] for item in predict_out["cognitiveFunctions"] 
        })

        out_row = pd.DataFrame(formatted_out, index=[0])
        out_rows.append(out_row)

    out_table = pd.concat(out_rows, ignore_index=True)
    out_table.to_csv(config.table_path, index=False, encoding="utf-8-sig")

    print(f"Table saved: {config.table_path}")
    print("Done :-)\n")