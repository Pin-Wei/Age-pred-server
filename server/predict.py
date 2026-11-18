#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import os
import warnings
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.exceptions import InconsistentVersionWarning

import util
from server import (
    MISSING_MARKER, 
    setup_logger, 
    register_exception_handlers, 
    authenticate_gitlab, 
)
from uvicorn_config import LOGGING_CONFIG


AGE_DIVIDE = 40
MISSING_THRESHOLD = 0.2
REPLACE_MISSING_WITH = 0.5  # since min-max scaler is used
MIN_PERCENTILE = 10
PRED_WITH_PERCENTILE = True
MAX_ADJUSTMENT = 20  # years
TOTAL_PARTICIPANTS = 412


logger = logging.getLogger(__name__)


class PredictConfig:
    def __init__(self):
        self.get_integrated_result_url = os.getenv("GET_INTEGRATED_RESULT_URL")
        self.process_textreading_url = os.getenv("PROCESS_TEXTREADING_URL")
        self.x_gitlab_token = os.getenv("X_GITLAB_TOKEN", "tcnl-project")
        self.local_headers = {
            "X-GitLab-Token": self.x_gitlab_token,
            "Content-Type": "application/json"
        }
        self.source_dir = Path(__file__).resolve().parent
        self.predicted_results_dir = self.source_dir / "predicted_results" # <id_card>_predicted_result.json
        self.model_dir = self.source_dir / "prediction" / "model" # <age_abb> (only trained on behavioral data); <age_abb>_ref.csv
        self.scaler_dir = self.source_dir / "prediction" / "scaler" # scaler_<age_full>.pkl (sklearn.preprocessing.MinMaxScaler() object; includes both behavioral and neuroimaging data); cognitive_percentiles_<age_full>.pkl
        self.cognitive_domains = {
            "工作記憶": ["MEMORY_OSPAN_BEH_LETTER_ACCURACY"],
            "情節記憶": [
                "MEMORY_EXCLUSION_BEH_C1_RECOLLECTION",
                "MEMORY_EXCLUSION_BEH_C2_RECOLLECTION",
                "MEMORY_EXCLUSION_BEH_C3_RECOLLECTION"
            ],
            "語言理解":  ["LANGUAGE_SPEECHCOMP_BEH_PASSIVE_ACCURACY"],
            "語言產出": ["LANGUAGE_READING_BEH_NULL_MeanSR"], 
            "動作": [
                "MOTOR_GOFITTS_BEH_ID1_LeaveTime",
                "MOTOR_GOFITTS_BEH_ID2_LeaveTime",
                "MOTOR_GOFITTS_BEH_ID3_LeaveTime",
                "MOTOR_GOFITTS_BEH_ID4_LeaveTime",
                "MOTOR_GOFITTS_BEH_ID5_LeaveTime",
                "MOTOR_GOFITTS_BEH_ID6_LeaveTime"
                # "MOTOR_GOFITTS_BEH_SLOPE_LeaveTime"
            ]
        }      


class PredictRequest(BaseModel):
    age: float
    id_card: str
    name: str
    test_date: str


class TextReadingProxyRequest(BaseModel):
    subject_id: str
    csv_filename: str


@lru_cache()
def get_predict_config() -> PredictConfig:
    load_dotenv()
    return PredictConfig()
    

@lru_cache(maxsize=4)
def load_model_and_scaler(age_abb: str, age_full: str, model_dir: Path, scaler_dir: Path):
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
    model = joblib.load(model_dir / age_abb)
    scaler = joblib.load(scaler_dir / f"scaler_{age_full}.pkl")
    return model, scaler


def correct_age_with_percentile(true_age: float, domain_score_list: List[Dict[str, Any]]) -> Tuple[float, float]:    ## Define the age group and the corresponding median age of the participant
    age_label = pd.cut(
        x=[true_age], 
        bins=[-np.inf, 24, 30, 35, 45, 55, 65, np.inf], 
        labels=["<24", "24-30", "30-35", "35-45", "45-55", "55-65", ">=65"]
    )[0]
    median_age = {
        "<24": 20, 
        "24-30": 27.5, 
        "30-35": 32.5, 
        "35-45": 40, 
        "45-55": 50, 
        "55-65": 60, 
        ">=65": 70
    }[str(age_label)]

    ## Calculate weighted domain scores (if valid)
    valid_weighted_scores = [
        (50 - x["score"]) / 50 for x in domain_score_list
        if x["score"] != -1
    ]
        # if the score is below average, positively increase the predicted age
        # e.g., if original score is 30, the weighted score is 0.4

    ## Calculate the impact of changes of each domain 
    num_valid = len(valid_weighted_scores)
    if num_valid == 0:
        prediction = median_age
    else:
        impact_per_domain = MAX_ADJUSTMENT / num_valid 
            # if MAX_ADJUSTMENT == 20, increase or decrease a maximum of 20 years
        prediction = median_age + sum(valid_weighted_scores) * impact_per_domain

    corrected_age = prediction 
    corrected_pad = corrected_age - true_age

    return corrected_pad, corrected_age


def correct_age_with_table(config: PredictConfig, true_age: float, prediction: float) -> Tuple[float, float]:
    age_abb = "y" if true_age < AGE_DIVIDE else "o"
    correction_ref = pd.read_csv(config.model_dir / f"{age_abb}_ref.csv")
    df = (
        pd.DataFrame({
            "real_age": [true_age],
            "pad": [prediction - true_age]
        })
        .assign(
            age_label=lambda x: pd.cut(
                x=x["real_age"], 
                bins=[-1, 24, 30, 35, 45, 55, 65, 100], 
                labels=["<25", "25-30", "30-35", "35-45", "45-55", "55-65", ">=65"]
            )
        )
        .assign(
            meanPAD=lambda x: x["age_label"].map(correction_ref.set_index("group")["meanPAD"]),
            sdPAD=lambda x: x["age_label"].map(correction_ref.set_index("group")["sdPAD"])
        )
    )
    corrected_pad = float(
        (df["pad"].values[0] - df["meanPAD"].values[0]) / df["sdPAD"].values[0]
    )
    corrected_age = float(prediction - corrected_pad) 

    return corrected_pad, corrected_age


def run_prediction(payload: PredictRequest, config: PredictConfig, timeout: int = 30) -> Dict[str, Any]:
    logger.info(f"Sending POST request to /get_integrated_result for subject: {payload.id_card}")
    try:
        res = requests.post(
            url=config.get_integrated_result_url,
            headers=config.local_headers,
            json={"subject_id": payload.id_card},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Request to get_integrated_result failed: {e}",
        )

    if res.status_code != 200:
        raise HTTPException(
            status_code=res.status_code,
            detail=f"Failed to get integrated result: {res.text}",
        )

    features_dict = res.json().get("integrated_result", {})
    true_age = payload.age
    predict_brain_age = (true_age != -1)

    age_abb = "y" if true_age < AGE_DIVIDE else "o"
    age_full = "young" if true_age < AGE_DIVIDE else "old"
    logger.info(f"Subject {payload.id_card} true age: {true_age} -> {age_full}")

    model, scaler = load_model_and_scaler(age_abb, age_full, config.model_dir, config.scaler_dir)

    ## Define platform features 
    if hasattr(model, "feature_names_in_"):
        platform_features = model.feature_names_in_
    elif hasattr(model, "feature_name_"):
        platform_features = model.feature_name_
    else:
        platform_features = util.init_platform_features()

    ## Prepare dataframe for prediction
    df = pd.DataFrame(index=range(1), columns=scaler.feature_names_in_)
    df.update(pd.DataFrame([features_dict]))

    ## Identify the positions of custom missing marker (e.g., -999; to ignore fields not listed in the json file)
    data_is_missing = (df == MISSING_MARKER)

    ## Replace missing values with 0 before scaling, and then fill with a specified value (0.5)
    df = df.astype(float).replace(MISSING_MARKER, np.nan).fillna(0)
    df_scaled = pd.DataFrame(scaler.transform(df), columns=scaler.feature_names_in_)
    df_scaled[data_is_missing] = REPLACE_MISSING_WITH

    ## Calculate the average scores for each cognitive domain
    domain_score_list = []
    for cog_domain, features in config.cognitive_domains.items():

        ## Find where the features are missing and calculate the missing ratio 
        feature_is_missing = data_is_missing[features]
        missing_ratio = feature_is_missing.sum(axis=1) / len(features)

        ## Calculate the average score of the features
        if missing_ratio.iloc[0] > MISSING_THRESHOLD:
            predict_brain_age = False
            domain_score_list.append({"name": cog_domain, "score": -1})
            logger.warning(f"Missing data in cognitive domain '{cog_domain}' for {payload.id_card}")
        else:
            avg_score = df_scaled[features].mean(axis=1).iloc[0]

            ## Interpolate the average score from (0, 1) to (0, 100)
            percentile = float(np.interp(x=avg_score, xp=[0, 1], fp=[0, 100]))

            ## Reverse the percentile if the cognitive domain is "動作"
            if cog_domain == "動作":
                percentile = 100.0 - percentile

            ## Avoid showing too low a score
            if percentile < MIN_PERCENTILE:
                logger.info(f"Score in '{cog_domain}' below minimum, resetting to {MIN_PERCENTILE}")
                percentile = float(MIN_PERCENTILE)

            score_val = int(round(percentile))
            domain_score_list.append({"name": cog_domain, "score": score_val})
            logger.info(f"{cog_domain} percentile: {score_val}")

    if predict_brain_age:
        logger.info("Predicting brain age ...")

        feats_not_scaled = [ f for f in platform_features if f not in df_scaled.columns ]
        if len(feats_not_scaled) > 0:
            logger.warning(f"Model features not in the scaler, set to {REPLACE_MISSING_WITH}: {feats_not_scaled}")
            df_scaled[feats_not_scaled] = REPLACE_MISSING_WITH

        prediction = float(model.predict(df_scaled[platform_features])[0])
        original_pad = prediction - true_age

        ## Perform brain-age correction 
        if PRED_WITH_PERCENTILE:
            corrected_pad, corrected_age = correct_age_with_percentile(true_age, domain_score_list)
        else:
            corrected_pad, corrected_age = correct_age_with_table(config, true_age, prediction)
    else:
        logger.warning(f"Brain age prediction skipped for {payload.id_card} due to missing data/age.")
        original_pad = -1.0
        corrected_pad = -1.0
        corrected_age = -1.0

    response = {
        "id_card": payload.id_card,
        "name": payload.name,
        "testDate": payload.test_date,
        "results": {
            "brainAge": f"{corrected_age:.2f}",
            "chronologicalAge": true_age,
            "originalPAD": f"{original_pad:.2f}",
            "ageCorrectedPAD": f"{corrected_pad:.2f}",
        },
        "cognitiveFunctions": domain_score_list,
        "meta": {
            "totalParticipants": TOTAL_PARTICIPANTS,
        },
    }
    logger.info(f"Brain age prediction result for {payload.id_card}: {response['results']['brainAge']}")
    logger.info(r"‧★,:*:‧\(^o^)/‧:*‧°★*")

    config.predicted_results_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.predicted_results_dir / f"{payload.id_card}_predicted_result.json"
    output_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")

    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger(__name__)
    cfg = get_predict_config()
    logger.info("predict service initialized.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(docs_url=None, lifespan=lifespan)
    register_exception_handlers(app, logger)

    @app.post("/predict")
    def predict_endpoint(
        payload: PredictRequest,
        token: str = Depends(authenticate_gitlab),
        config: PredictConfig = Depends(get_predict_config),
    ):
        logger.info(f"Received /predict request for subject ID: {payload.id_card}")
        return run_prediction(payload, config)
    

    @app.post("/process_textreading")
    def process_textreading_proxy(
        payload: TextReadingProxyRequest,
        config: PredictConfig = Depends(get_predict_config),
    ):
        logger.info(f"Forwarding /process_textreading request for subject ID: {payload.subject_id}")
        try:
            resp = requests.post(
                url=config.process_textreading_url,
                headers=config.local_headers,
                json=payload.model_dump(),
                timeout=120,
            )
        except requests.RequestException as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to process_textreading service: {e}",
            )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Failed to forward process_textreading request: {resp.text}",
            )

        return {"message": "Successfully forwarded request to process_textreading", "data": resp.json()}

    return app


if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8888, log_config=LOGGING_CONFIG)