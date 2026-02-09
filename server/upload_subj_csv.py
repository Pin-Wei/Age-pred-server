#!/usr/bin/env python

import os
import sys
import requests

from dotenv import load_dotenv
load_dotenv()

qoca_token = os.getenv("QOCA_TOKEN")
qoca_headers = {
    "Authorization": f"Bearer {qoca_token}"
}

def force_google_style_csv(input_path):
    fp, ext = os.path.splitext(input_path)
    assert ext == ".csv", "Input file must be in CSV format."

    with open(input_path, "rb") as f:
        content = f.read()

    try:
        content.decode("utf-8")
        return input_path
    
    except UnicodeDecodeError:
        print("Converting to UTF-8 ...")
        try:
            content = content.decode("cp950")
        except UnicodeDecodeError:
            content = content.decode("big5", errors="strict")

        output_path = f"{fp}+{ext}"
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            
        return output_path

def upload_file(file_path):
    if not os.path.exists(file_path):
        raise ValueError(f"File {file_path} does not exist :(")
    
    else:
        print(f"Uploading file: '{file_path}'")
        ext = os.path.splitext(file_path)[1].lower()

        if ext != ".csv":
            raise ValueError(f"Unsupported file type: {ext}")
            
        with open(file_path, 'rb') as f:
            res = requests.post(
                url='https://qoca-api.chih-he.dev/uploadfile', 
                headers=qoca_headers, 
                files={'file': f},
            )
    
    if res.status_code == 200:
        print("Successfully uploaded file :)")
    else:
        print("Failed to upload file :(")
        raise ValueError(f"Failed to upload file: {res.status_code}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        raw_input_path = os.path.join("subj_csv_files", sys.argv[1])
    else:
        raw_input_path = os.path.join("subj_csv_files", "test_and_NHRI_2025-09-15.csv")

    file_path = force_google_style_csv(raw_input_path)    
    upload_file(file_path)