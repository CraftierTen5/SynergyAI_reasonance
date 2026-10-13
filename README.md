# CivicResolve AI: complaint extraction prototype

This prototype keeps the existing complaint ingestion API and CSV data. It adds an on-demand Gemini extraction module that returns a validated summary and structured fields. It does not classify complaints for automatic action, reject or close them, assign departments, or connect to a database. Multilingual extraction is experimental; accuracy is not guaranteed.

CSV processing defaults to `data/sample_complaints.csv`, whose complaint text column is `original_text`. Results go to `data/analysis_results.csv`; the input is never overwritten. CSV storage and processing are suitable for a hackathon prototype, not unrestricted concurrent production traffic.

## 1. Open the project in PowerShell

Open PowerShell and move to the project folder (adjust the path if you moved the project):

```powershell
Set-Location "C:\Users\ishit\OneDrive\Desktop\Work\SynergyAI_reasonance"
```

## 2. Create and activate a Python environment

A virtual environment is a private Python package area for this project. Python 3.11 or newer is required. The first command creates it; the second activates it for this PowerShell window.

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

If `py -3.11` is unavailable, install Python 3.11+ and reopen PowerShell. The execution policy command only applies to the current PowerShell process.

## 3. Install packages

This installs FastAPI, Pydantic, the official Google GenAI SDK, dotenv, and test tools into the active virtual environment:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 4. Configure your Gemini API key privately

The `.env` file is a local configuration file that the app reads when it starts. `.env.example` contains only a placeholder. If `.env` does not exist, copy the example; if it already exists, keep it and edit it locally so you do not overwrite your settings:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

In Notepad, replace `PASTE_YOUR_API_KEY_HERE` on the `GEMINI_API_KEY=` line with your key from Google AI Studio. Keep the key only in `.env`. The other line selects the model; the default is `gemini-2.5-flash`. Save and close Notepad. `.env` is excluded by `.gitignore`; never commit it, put the key in Python code, or paste it into chat. If you expose a key, revoke it in Google AI Studio and create a replacement.

Complaint routing is deterministic. To customize department labels, add `CIVIC_DEPARTMENT_WATER_SUPPLY=...` (and corresponding `CIVIC_DEPARTMENT_ROADS`, `CIVIC_DEPARTMENT_DRAINAGE`, `CIVIC_DEPARTMENT_STREET_LIGHTING`, `CIVIC_DEPARTMENT_WASTE_MANAGEMENT`, `CIVIC_DEPARTMENT_SANITATION`, `CIVIC_DEPARTMENT_PUBLIC_SAFETY`, `CIVIC_DEPARTMENT_ELECTRICITY`, or `CIVIC_DEPARTMENT_OTHER`) to `.env`. `CIVIC_GENERAL_DEPARTMENT` and `CIVIC_MANUAL_REVIEW_DEPARTMENT` configure the fallback destinations. `other` and unknown categories require human review; safety-critical analyses always require human review.

Google currently lists `gemini-2.5-flash` as a model with structured output support. Google notes that access to 2.5 models may be limited for accounts that have not used them before, and recommends newer models for new projects. The default is retained as requested; if your account reports that it is unavailable, use `gemini-3.8-flash`, which Google currently lists as stable and supports structured outputs. Model availability can depend on account and region. See the [official model list](https://ai.google.dev/gemini-api/docs/models), [Gemini 2.5 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash), [Gemini 3.8 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), and [structured output guide](https://ai.google.dev/gemini-api/docs/structured-output).

## 5. Start the API

This starts the local web server. Keep this PowerShell window open while using the API:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs> in a browser to see and try the interactive API documentation. An API endpoint is a URL that accepts a defined request; the `/docs` page lets you send requests without writing a separate client.

## 6. Analyze one complaint first

Use the `/analyze` endpoint from another PowerShell window. This sends one complaint to Gemini and returns its original text plus validated structured analysis. This uses API quota.

```powershell
$body = @{
  complaint_id = "demo-001"
  original_text = "Namma street alli 3 dina inda neeru barthilla"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/analyze" -Method Post -ContentType "application/json" -Body $body
```

The response includes `detected_language`, an English summary, one allowed category, explicit location/duration when present, urgency indicators, and review flags. The model does not make decisions about closing or rejecting the complaint.

## 7. Process a small CSV sample

First process five rows to check latency and API usage. Set `max_rows` to 10 for ten rows or omit it / set it to `null` to process the full file. Do not run the full file until you have reviewed the small sample. This makes one Gemini request per non-empty complaint.

```powershell
$body = @{
  input_path = "sample_complaints.csv"
  output_path = "analysis_results.csv"
  max_rows = 5
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/analyze-csv" -Method Post -ContentType "application/json" -Body $body
```

Only CSV files inside this project's `data` directory can be selected by this API. The response reports rows read, successful analyses, failures, skipped empty rows, and the output path. Open `data/analysis_results.csv` in Excel or a text editor. Original input fields and complaint text remain in each output row; analysis list fields are JSON strings. Failed requests keep their original row and have `analysis_status=error` with a short safe reason. Empty text rows are marked `skipped_empty_text`.

## 8. Run tests without using API quota

The normal test suite mocks the Gemini provider; it does not need a key and does not make real Gemini requests:

```powershell
python -m pytest -q
```

## Troubleshooting and privacy

- **Missing, placeholder, or rejected key:** confirm `.env` is in the project root and that its `GEMINI_API_KEY=` line contains a valid, active Google AI Studio key with no placeholder text. If Google reports the key is invalid, create or copy a replacement key from Google AI Studio into `.env`, then restart the server. Never share the key; the app reports authentication problems without printing it.
- **Invalid/unavailable model:** check the exact model ID and account access in Google AI Studio and the official model list linked above; update `GEMINI_MODEL` in `.env`, then restart.
- **Rate limit:** wait and retry a small sample. The endpoint returns a rate-limit response and CSV processing marks that row as failed without logging the provider payload.
- **Network error or timeout:** check your internet connection and retry; the affected CSV row is preserved for a later retry.
- **Permission error:** close applications holding the result CSV, ensure the project `data` directory is writable, and retry.
- **Safety and multiple issue checks:** simple English phrase checks flag obvious starter cases such as live wires, electric shocks, structural danger, gas leaks, trapped people, and open manholes. A small English phrase check also flags reports that appear to name multiple issue types joined together. They are only preliminary safeguards. They do not reliably understand negation, context, Hindi/Kannada, transliteration, synonyms, or spelling variations; human review remains necessary.

### macOS/Linux

The equivalent environment commands are:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env  # only if .env does not already exist
python -m uvicorn app.main:app --reload
```
