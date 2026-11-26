# Age-pred-server

Automated backend server for cognitive assessment preprocessing, brain-age prediction, and personalized PDF report delivery.

When participants complete five cognitive tasks on Pavlovia (*GoFitts*, *Operation Span*, *Speech Comprehension*, *Exclusion*, and *Text Reading*), GitLab webhooks trigger a set of non-blocking FastAPI microservices under `server/` that download experimental records, process them and derive behavioral features, run age-stratified brain-age prediction and normative percentile scoring, and dispatch results to an external reporting API (QOCA) for PDF generation and participant emailing.

> **Privacy & Security Notice:** Participant data (`data/`), environment credentials (`.env`), and private tokens are excluded from this repository.

## 1. Architecture Overview
The backend is organized around four independent FastAPI + Uvicorn microservices inside `server/`, managed together in a 4-pane `tmux`session under a`systemd`user service, alongside scheduled`cron` jobs for audio retrieval, task polling, and Dropbox backups.

| Service                                                                                                            | Port   | Primary Responsibility                                                                              | Key Endpoints                                |
| :------------------------------------------------------------------------------------------------------------------- | :------- | :---------------------------------------------------------------------------------------------------- | :--------------------------------------------- |
| [`server.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/server.py)                               | `8000` | GitLab webhook ingestion, task routing, and manual report triggering                                | `GET /`, `POST /webhook`, `POST /report`     |
| [`get_integrated_result.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/get_integrated_result.py) | `7777` | Reads per-subject integrated feature JSON files                                                     | `POST /get_integrated_result`                |
| [`predict.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/predict.py)                             | `8888` | Age-stratified brain-age prediction, PAD calibration, cognitive percentiles, and Text Reading proxy | `POST /predict`, `POST /process_textreading` |
| [`process_textreading.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/process_textreading.py)     | `6666` | Speech audio conversion, silence trimming, Whisper transcription, and speech-rate extraction        | `POST /process_textreading`                  |

## 2. End-to-End Pipeline
- **Webhook Ingestion (`server.py`)**: Receives push events at `POST /webhook` (authenticated via `X-GitLab-Token`). Filters for `.csv` files added by `Pavlovia Committer` or `Local Committer` and enqueues processing with FastAPI `BackgroundTasks`.

- **Feature Generation (`server/data_processors/` & `task_integrator.py`)**:
  - **GoFitts, O-Span, Speech Comprehension, Exclusion**: Downloads the newly committed CSV, runs the corresponding processor, and merges generated features into `{subject_id}_integrated_result.json`
  - **Text Reading**: Registers a QOCA `exam` and `task` entry so audio retrieval and downstream scoring can run asynchronously.

- **Audio Download & Speech-Rate Processing (`download_textreading_files.py` → `process_textreading.py`)**:
  - Every `10` minutes, `download_textreading_files.py`checks subjects with`*Z.csv`records whose`.webm`audio files are missing, downloads them from the Pavlovia media API via`sessionToken`, and invokes `POST /process_textreading`.
  - `TextReadingProcessor` converts audio (`ffmpeg`), strips silence (`pydub`), transcribes Mandarin speech (`whisper-timestamped`), computes `mean_speech_rate` (`LANGUAGE_READING_BEH_NULL_MeanSR`), and updates the integrated JSON.

- **Task Polling, Prediction & Report Delivery (`process_tasks.py` → `predict.py`)**:
  - Every `10` minutes, `process_tasks.py`(guarded by`../logs/processTasks.lock`) triggers `/process_textreading`, queries participant demographics from QOCA API (`/user/{subject_id}`), and calls `POST /predict`.
  - `predict.py` scores all five cognitive domains, predicts and calibrates brain age, saves `predicted_results/{id_card}_predicted_result.json`, and returns the payload for QOCA PDF report generation and emailing.

- **Scheduled Backup (`upload_to_dropbox.py`)**: Runs weekly on `Mondays` at `08:00`via`cronjob.sh`.

## 3. Cognitive Task Processors (`server/data_processors/`)
Each Pavlovia task is parsed by a dedicated processor module under `server/data_processors/`:

| Processor | Cognitive Domain | Input & Key Columns | Extracted Output Features |
| :--- | :--- | :--- | :--- |
| [`ospan_processor.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/data_processors/ospan_processor.py) | Working Memory (`工作記憶`) | PsychoPy CSV (`指定代號`, `MathResult`, `LetterResult`; last 30 non-empty trials) | `ID`, `MEMORY_OSPAN_BEH_MATH_ACCURACY`, `MEMORY_OSPAN_BEH_LETTER_ACCURACY` |
| [`exclusion_processor.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/data_processors/exclusion_processor.py) | Episodic Memory (`情節記憶`) | PsychoPy CSV (`指定代號`, `number_of_cue_t`, `key_resp.keys` / `key_resp_keys`, `key_resp.rt`, `stimuli_t`; last 54 non-empty trials) | 42 features across `C1`–`C3`: `FAMILIARITY`, `RECOLLECTION`, and `_PROPORTION` / `_RT` for `TarHit`, `TarMiss`, `NonTarFA`, `NonTarCR`, `NewFA`, `NewCR` |
| [`speechcomp_processor.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/data_processors/speechcomp_processor.py) | Language Comprehension (`語言理解`) | PsychoPy CSV (filtered to `condition == 'passive'`; `stim_resp.corr`, `duration`) | `ID`, `SPEECHCOMP_PASSIVE_ACCURACY` (0–100%), `SPEECHCOMP_PASSIVE_RT` (mean RT of correct trials) |
| [`textreading_processor.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/data_processors/textreading_processor.py) | Language Production (`語言產出`) | `.webm` or `.wav` recordings → `ffmpeg` conversion → `pydub` silence removal (`*_ds.wav`) → `whisper-timestamped` (`base` Chinese model → `*.words.csv`) | `mean_speech_rate` (`LANGUAGE_READING_BEH_NULL_MeanSR`, syllables/sec computed over `[\u4e00-\u9fff0-9]+`) |
| [`gofitts_processor.py`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/data_processors/gofitts_processor.py) | Motor Function (`動作`) | PsychoPy CSV (`指定代號`, `sequence_loop.thisN`, `trial_loop.thisN`, `from`, `to`, `mouse.x`, `mouse.y`, `mouse.time`, `w`, `a`, `leave_time`) → `.sd3` → `java -jar GoFitts_modified.jar -p` | `GoFitts-{subject_id}-summary.csv` with `GOFITTS_BEH_ID{0..n}_{LeaveTime,PointTime,Throughput}` and `GOFITTS_BEH_SLOPE_{LeaveTime,PointTime,Throughput}` |

## 4. Brain-Age Prediction & Calibration (`server/predict.py`)
`predict.py` loads age-stratified behavioral models and scalers from [`server/prediction/model/`](https://github.com/Pin-Wei/Age-pred-server/tree/main/server/prediction/model) and [`server/prediction/scaler/`](https://github.com/Pin-Wei/Age-pred-server/tree/main/server/prediction/scaler):

- **Age Stratification (`AGE_DIVIDE = 40`)**:
  - Age < `AGE_DIVIDE` → young (y)
  - Age >= `AGE_DIVIDE` → old (o)
  - File names: `model/{y|o}`, `model/{y|o}_ref.csv`, `scaler/scaler_{young|old}.pkl`, `scaler/cognitive_percentiles_{young|old}.pkl`

- **Missing-Value Handling**: Missing entries encoded as `-999` are temporarily set to `0` prior to `MinMaxScaler` transformation and then imputed with `REPLACE_MISSING_WITH = 0.5`. If a domain's missing-value ratio exceeds `MISSING_THRESHOLD = 0.2`, that domain receives `-1` and brain-age prediction is skipped (`predict_brain_age = False`).

- **Cognitive Domain Percentiles**: Scaled features per domain are averaged and linearly mapped to `[0, 100]` percentiles (with `MIN_PERCENTILE = 10` floor). Motor domain (`動作`) is reverse-scored (`100 - percentile`).

- **Age Calibration**:
  - **Percentile Calibration (default, `PRED_WITH_PERCENTILE = True`)**: `correct_age_with_percentile` assigns chronological age to one of 7 bins (`<24`, `24-30`, `30-35`, `35-45`, `45-55`, `55-65`, `>=65`) with reference medians `[20, 27.5, 32.5, 40, 50, 60, 70]`, computes a bounded adjustment scaled by `MAX_ADJUSTMENT / num_valid` (`MAX_ADJUSTMENT = 20`), and derives `ageCorrectedPAD = corrected_age - true_age`.
  - **Reference-Table Calibration (fallback, `PRED_WITH_PERCENTILE = False`)**: `correct_age_with_table` normalizes PAD against `meanPAD` and `sdPAD` from `{y|o}_ref.csv` across bins (`<25`, `25-30`, `30-35`, `35-45`, `45-55`, `55-65`, `>=65`).

## 5. Repository Structure
```text
Age-pred-server/
├── environment.yml                 # Conda environment specification (Python 3.12, CUDA 12.9)
├── prepare_env.sh                  # Alternative Conda + pip environment bootstrap script
├── correcting_sid.sh               # Subject ID correction helper script
├── data/                           # Experimental data directory (excluded from git)
└── server/
    ├── server.py                   # Main FastAPI webhook & report server (port 8000)
    ├── get_integrated_result.py    # Integrated JSON lookup service (port 7777)
    ├── predict.py                  # Brain-age prediction & percentile service (port 8888)
    ├── process_textreading.py      # Speech audio processing service (port 6666)
    ├── task_integrator.py          # TaskIntegrator & result formatting logic
    ├── util.py                     # Shared utility functions
    ├── uvicorn_config.py           # Custom Uvicorn logging configuration
    ├── onetime_setup.sh            # Registers & starts systemd user service (pinwei.service)
    ├── start_service.sh            # Launches 4-pane tmux session running all FastAPI services
    ├── cronjob.sh                  # Manages cron schedules (list / enable / disable / clean)
    ├── download_textreading_files.py  # Polls Pavlovia media API for missing .webm audio files
    ├── process_tasks.py            # Polls pending tasks and orchestrates prediction pipeline
    ├── upload_to_dropbox.py        # Weekly backup script to Dropbox
    ├── trigger_webhook.py          # Manually triggers /webhook for a single commit/file
    ├── trigger_webhook_batch.py    # Batch-triggers /webhook
    ├── trigger_predict_from_csv.py # Runs batch brain-age predictions from a CSV file
    ├── tidy_predicted_results.py   # Aggregates predicted results into tabular format
    ├── upload_subj_csv.py          # Uploads participant metadata & email CSV to external API
    ├── data_processors/
    │   ├── exclusion_processor.py
    │   ├── gofitts_processor.py
    │   ├── GoFitts_modified.jar    # Java utility invoked by gofitts_processor.py
    │   ├── ospan_processor.py
    │   ├── speechcomp_processor.py
    │   └── textreading_processor.py
    ├── prediction/
    │   ├── model/                  # Pre-trained models & reference tables (y, y_ref.csv, o, o_ref.csv)
    │   └── scaler/                 # MinMaxScalers & cognitive percentile references (.pkl)
    ├── patches/                    # Data patching scripts
    └── ARCHIVED/                   # Deprecated legacy scripts
```

## 6. API Reference
Protected endpoints require the `X-GitLab-Token` HTTP header matching the `X_GITLAB_TOKEN` environment variable (default: `tcnl-project`). All services bind to `0.0.0.0` via `uvicorn.run` with no CLI arguments.

### 6.1 Main Server (`server.py`)
| Endpoint | Auth | Request Body | Response |
| :--- | :--- | :--- | :--- |
| `GET /` | None | — | `{"status": "ok"}` |
| `POST /webhook` | `X-GitLab-Token` | `{"project": {...}, "commits": [...]}` | `{"status": "ok", "queued_files": [...]}` or `{"status": "ignored", "reasons": [...]}` |
| `POST /report` | None | `{"subject_id": "<SUBJECT_ID>"}` | `{"status": "ok", "exam_id": <int>}` (or `422` / `502` on error) |

### 6.2 Integrated Result Service (`get_integrated_result.py`)
| Endpoint | Auth | Request Body | Response |
| :--- | :--- | :--- | :--- |
| `POST /get_integrated_result` | `X-GitLab-Token` | `{"subject_id": "<SUBJECT_ID>"}` | `{"status": "ok", "integrated_result": {...}}` (`404` if missing, `500` on parse error) |

### 6.3 Prediction Service (`predict.py`)
| Endpoint | Auth | Request Body | Response |
| :--- | :--- | :--- | :--- |
| `POST /predict` | `X-GitLab-Token` | `{"age": 45.0, "id_card": "...", "name": "...", "test_date": "..."}` | Prediction payload (see example below); also saved to `predicted_results/{id_card}_predicted_result.json` |
| `POST /process_textreading` | None | `{"subject_id": "...", ...}` | Proxy response forwarding to `process_textreading.py`: `{"message": "...", "data": {...}}` |

Example `POST /predict` response:
```json
{
  "id_card": "A123456789",
  "name": "Participant Name",
  "testDate": "2026-01-15",
  "results": {
    "brainAge": "42.30",
    "chronologicalAge": 45,
    "originalPAD": "-2.70",
    "ageCorrectedPAD": "-1.85"
  },
  "cognitiveFunctions": [
    { "name": "工作記憶", "score": 75.0 },
    { "name": "情節記憶", "score": 68.5 },
    { "name": "語言理解", "score": 82.0 },
    { "name": "語言產出", "score": 71.2 },
    { "name": "動作", "score": 64.0 }
  ],
  "meta": {
    "totalParticipants": 412
  }
}
```

### 6.4 Text Reading Service (`process_textreading.py`)
| Endpoint | Auth | Request Body | Response |
| :--- | :--- | :--- | :--- |
| `POST /process_textreading` | `X-GitLab-Token` | `{"subject_id": "...", "csv_filename": "..."}` | `{"status": "success", "message": "...", "mean_speech_rate": 4.25, "success": true, "files_processed": [...]}` (`404` / `422` / `500` on error) |

## 7. Installation & Configuration

### 7.1 System Requirements
- **OS**: Linux (requires `systemd --user`, `tmux`, and `cron`)
- **Python**: Python `3.12` via Conda / Miniforge
- **System Tools**: `ffmpeg`, Java Runtime (required for `GoFitts_modified.jar`), and CUDA `12.9` (for GPU-accelerated Whisper / PyTorch inference as pinned in `environment.yml`)

### 7.2 Create the Conda Environment
```bash
conda env create -f environment.yml

# Alternative setup if environment.yml fails to resolve:
bash prepare_env.sh
```
Key packages installed include `fastapi`, `uvicorn`, `requests`, `python-dotenv`, `pytorch`, `torchaudio`, `onnxruntime`, `whisper-timestamped`, `pydub`, `numpy`, `pandas`, `scikit-learn`, `lightgbm`, and `joblib`.

### 7.3 Configure Environment Variables (`.env`)
Create a `.env` file with your credentials, service endpoints, and Pavlovia project mappings:

| Variable | Description |
| :--- | :--- |
| `X_GITLAB_TOKEN` | Shared secret expected in `X-GitLab-Token` headers (default: `tcnl-project`) |
| `GITLAB_TOKEN` | GitLab / Pavlovia personal access token for downloading CSVs and media files |
| `QOCA_URL` | External QOCA reporting API base URL (default: `https://qoca-api.chih-he.dev`) |
| `PREDICT_URL` | Endpoint URL for `predict.py` (`POST /predict`) |
| `GET_INTEGRATED_RESULT_URL` | Endpoint URL for `get_integrated_result.py` (`POST /get_integrated_result`) |
| `PROCESS_TEXTREADING_URL` | Endpoint URL for `process_textreading.py` (`POST /process_textreading`) |
| `DISCORD_BOT1_WEBHOOK_URL` | Discord webhook URL for server notifications and alerts |
| `DISCORD_USER1_ID` | Discord user ID mentioned in alerts |
| `EXPERIMENT_GOFITT_NAME` | Pavlovia project name for the GoFitts task |
| `EXPERIMENT_OSPAN_NAME` | Pavlovia project name for the Operation Span task |
| `EXPERIMENT_SPEECHCOMP_NAME` | Pavlovia project name for the Speech Comprehension task |
| `EXPERIMENT_EXCLUSION_NAME` | Pavlovia project name for the Exclusion task |
| `EXPERIMENT_TEXTREADING_NAME` | Pavlovia project name for the Text Reading task |

## 8. Deployment & Operations

### 8.1 Running the Microservices (`systemd` + `tmux`)
Before running the setup script on a new machine, verify the working directory path inside [`server/start_service.sh`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/start_service.sh).

```bash
cd server
./onetime_setup.sh
```

[`onetime_setup.sh`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/onetime_setup.sh) installs and enables `~/.config/systemd/user/<SERVICE_NAME>` (`Type=forking`, `Restart=on-failure`), which invokes `start_service.sh` to launch all four Python services in a 4-pane `tmux` session named `<SESSION>`.

```bash
# Inspect live logs for all four services
tmux attach -t <SESSION>

# Manage via systemd
systemctl --user status <SERVICE_NAME>
systemctl --user restart <SERVICE_NAME>
```

### 8.2 Managing Scheduled Cron Jobs (`server/cronjob.sh`)
Use [`cronjob.sh`](https://github.com/Pin-Wei/Age-pred-server/blob/main/server/cronjob.sh) to manage recurring background tasks (logs are written to `../logs/cronjob_*.log`):

```bash
cd server

# List current cron entries
bash cronjob.sh list

# Enable jobs
bash cronjob.sh enable download_textreading_files   # Every 10 min (*/10 * * * *)
bash cronjob.sh enable process_tasks                # Every 10 min (*/10 * * * *)
bash cronjob.sh enable upload_to_dropbox            # Mondays at 08:00 (0 8 * * 1)

# Disable a specific job or clear all managed jobs
bash cronjob.sh disable <job_name>
bash cronjob.sh clean
```

### 8.3 Manual & Maintenance Utilities (`server/`)
| Script | Purpose |
| :--- | :--- |
| `trigger_webhook.py` | Manually simulates a GitLab webhook commit to `POST /webhook` |
| `trigger_webhook_batch.py` | Batch-triggers `POST /webhook` across multiple records |
| `trigger_predict_from_csv.py` | Triggers batch brain-age predictions from a CSV input file |
| `tidy_predicted_results.py` | Consolidates individual prediction JSON files into a summary table |
| `upload_subj_csv.py` | Uploads participant ID and email records to the external reporting API |
| `upload_to_dropbox.py` | Backs up data/results to Dropbox (also scheduled weekly via `cronjob.sh`) |
