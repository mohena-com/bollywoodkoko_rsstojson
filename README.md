# Bollywood Hungama RSS Extractor

Extracts stories published on a selected date from seven Bollywood Hungama RSS feeds.

## Files

- `bh_rss_extractor.py` - RSS extraction program
- `config.yaml` - feeds, labels, timezone and external output path
- `requirements.txt` - Python dependencies

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you are using Command Prompt instead of PowerShell, run:

```cmd
.venv\Scripts\activate.bat
```

## Configure output

Edit `config.yaml`:

```yaml
output:
  folder: "/path/to/external/output"
  filename: "bollywood_hungama_today.json"
```

The JSON is written outside this workspace when an external path is configured.

## Run

Today's date in Asia/Kolkata:

```bash
python bh_rss_extractor.py
```

Specific date:

```bash
python bh_rss_extractor.py --date 2026-09-12
```

The program converts RSS publication timestamps to the configured timezone before filtering by date.
