#!/usr/bin/env python

import os
import re
import json
import glob
import numpy as np
import pandas as pd

data_rows = []

for fp in glob.glob(os.path.join("predicted_results", "*.json")):

    ## Ensuring that the file name match the pattern "????s????-?_*.json" where each ? is a digit
    if (
        not re.match(r"[\d]{4}s[\d]{4}-[\d]{1}_.+\.json", os.path.basename(fp))
        and not os.path.basename(fp).startswith("05020005")
        and not os.path.basename(fp).startswith("65110002-2")
        and not os.path.basename(fp).startswith("65010001-1")
    ):
        continue 

    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)

    ## Cognitive functions are stored in a list of dictionaries, so we need to convert it to a dictionary:
    cognitive_functions = { 
        item["name"]: item["score"] for item in data["cognitiveFunctions"] 
    }

    ## Extracting the necessary fields from the JSON data:
    selected_fields = {
        "Date": data["testDate"], 
        "SID": data["id_card"], 
        "Name": data["name"], 
        "Age": data["results"]["chronologicalAge"],
        "Brain Age": data["results"]["brainAge"],
        "PAD": data["results"]["originalPAD"],
        "Corrected PAD": data["results"]["ageCorrectedPAD"]
    }
    selected_fields.update(cognitive_functions)
    # selected_fields["Avg"] = np.mean([ v for v in cognitive_functions.values() if v != -1 ])
    selected_fields["Avg"] = np.mean(list(cognitive_functions.values())) if not any(v == -1 for v in cognitive_functions.values()) else -1

    ## Adding platform features:
    fp2 = os.path.join("integrated_results", f"{data['id_card']}_integrated_result.json")
    with open(fp2, "r", encoding="utf-8") as f2:
        platform_features = json.load(f2)

    platform_features = {
        k: v if v != -999 else np.nan 
        for k, v in platform_features.items()
    }
    selected_fields.update(platform_features)    
    data_row = pd.DataFrame(selected_fields, index=[0])

    data_rows.append(data_row)

df = pd.concat(data_rows, ignore_index=True)

## Saving the DataFrame to a CSV file:
out_path = os.path.join("predicted_results", "tidy_predicted_results.csv")
df.to_csv(out_path, index=False, encoding="utf-8-sig")
print(f"\n{len(data_rows)} results are organized into a table and saved to:\n{out_path}\n")

