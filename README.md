# AAPC Medical Coding Scraper

A set of Selenium-based Python scrapers that collect medical coding reference data from the **AAPC Codify** web portal: CPT®, HCPCS Level II, modifiers, lay terms, guidelines and ICD-10-CM "Excludes1" relationships. The repo also includes the cleaned, compiled dataset organised by code category.

**Author:** Atharva Kanase ([atharvakanase17@gmail.com](mailto:atharvakanase17@gmail.com))

---

## Table of contents

- [What's collected](#whats-collected)
- [Repository structure](#repository-structure)
- [Scrapers](#scrapers)
- [Dataset](#dataset)
- [Setup](#setup)
- [Usage](#usage)
- [How the scrapers work](#how-the-scrapers-work)
- [Disclaimer](#disclaimer)

---

## What's collected

| Data | Fields |
|---|---|
| **CPT codes**: Category I (00100–99607), Category II (F), Category III (T), PLA (U), MAAA (M) | code, short description, lay term, guidelines, code range, source URL, timestamp |
| **E/M codes** (98000–99499) | same as CPT |
| **HCPCS Level II**: J series (drugs), Q series | code, short description, lay term, guidelines |
| **CPT modifiers** | modifier code, description, lay term |
| **HCPCS modifiers** | modifier code, descriptor, lay term, guidelines |
| **Modifier crosswalk ("Modifier X")**: which modifiers are allowed on each CPT code | CPT code, modifier, description, allowed entity |
| **ICD-10-CM Excludes1**: for each ICD category/code, every Excludes1 code | main code, main description, all Excludes1 codes |
| **HCPCS drug rules**: chemo vs non-chemo classification | HCPCS code, drug name, brand name, billing unit, classification, JW/JZ modifier applicability |

## Repository structure

```
aapc-medical-coding-scraper/
├── scrapers/
│   ├── cpt_range_scraper.py          # CPT range: description, guidelines, lay term (+ CPT modifiers mode)
│   ├── cpt_list_scraper.py           # CPT codes from an Excel list + Modifier X crosswalk
│   ├── cpt_modifier_x_scraper.py     # CPT range with Crosswalks → Modifiers extraction
│   ├── cpt_layterm_backfill.py       # Re-scrapes lay terms for codes missed earlier
│   ├── hcpcs_code_scraper.py         # HCPCS Level II range (J, Q series …)
│   ├── hcpcs_modifier_scraper.py     # HCPCS modifiers
│   └── legacy/
│       ├── cpt_icd10_crosswalk.py    # Early CPT → ICD-10-CM crosswalk extractor
│       └── cpt_icd10_crosswalk_v2.py # Improved version of the above
│
├── data/
│   ├── cpt/
│   │   ├── anesthesia_00100-01999/
│   │   ├── surgery_10004-69990/            # split into 1xxxx … 6xxxx files
│   │   ├── radiology_70010-79999/
│   │   ├── pathology_lab_80047-89398/
│   │   ├── medicine_90281-99607/
│   │   ├── evaluation_management_98000-99499/
│   │   ├── category_ii_0001F-9007F/
│   │   ├── category_iii_0042T-0987T/
│   │   ├── pla_0001U-0599U/
│   │   ├── maaa_0002M-0020M/
│   │   └── layterm_backfill/
│   ├── hcpcs/
│   │   ├── j_series/
│   │   ├── q_series/
│   │   └── chemo_non_chemo_rules/
│   ├── modifiers/
│   │   ├── cpt_modifiers/
│   │   ├── hcpcs_modifiers/
│   │   └── cpt_modifier_x/                 # split by first digit 0–9 + master file
│   └── icd10/
│       └── icd10_excludes1.xlsx
│
├── requirements.txt
├── .gitignore
└── README.md
```

**Naming convention:** every folder and file is `lowercase_snake_case`. Each CPT folder is named `<section>_<code-range>`, and its files repeat the folder name with a `cpt_` prefix, e.g. `data/cpt/radiology_70010-79999/cpt_radiology_70010-79999.csv`. Most datasets ship as both **`.csv`** (for code) and **`.xlsx`** (for Excel).

## Scrapers

All scrapers live in [`scrapers/`](scrapers/). Run them **from the repository root** so the relative paths (`./chromedriver/…`, `./outputs/`) resolve correctly.

| Script | Purpose | Input | Produces |
|---|---|---|---|
| [cpt_range_scraper.py](scrapers/cpt_range_scraper.py) | Search → scrape → "Next" loop over a CPT range. Captures description, guidelines and lay term. `--mode modifiers` scrapes the CPT modifiers list instead. | `--start-code`, `--stop-code` | `data/cpt/*`, `data/modifiers/cpt_modifiers/` |
| [cpt_list_scraper.py](scrapers/cpt_list_scraper.py) | Reads CPT codes from `cpt_codes.xlsx` (column `CPT_CODE`) and scrapes each code's page and Modifier X crosswalk. | `cpt_codes.xlsx` | `data/modifiers/cpt_modifier_x/` |
| [cpt_modifier_x_scraper.py](scrapers/cpt_modifier_x_scraper.py) | Range scraper that also opens **Crosswalks → Modifiers** and the Modifier X modal on each code. | `--start-code`, `--stop-code` | `data/modifiers/cpt_modifier_x/` |
| [cpt_layterm_backfill.py](scrapers/cpt_layterm_backfill.py) | Back-fills lay terms for a list of codes that earlier runs missed. | `--input-file` Excel, `--cpt-column` | `data/cpt/layterm_backfill/` |
| [hcpcs_code_scraper.py](scrapers/hcpcs_code_scraper.py) | Same loop with HCPCS-specific selectors (`--code-type HCPCS`). | `--start-code`, `--stop-code`, `--code-type` | `data/hcpcs/j_series/`, `data/hcpcs/q_series/` |
| [hcpcs_modifier_scraper.py](scrapers/hcpcs_modifier_scraper.py) | Scrapes HCPCS modifiers (descriptor, lay term, guidelines) with fallback XPaths. | `--start-code`, `--stop-code` | `data/modifiers/hcpcs_modifiers/` |
| [legacy/](scrapers/legacy/) | Earlier scripts that log in to AAPC and extract the CPT → ICD-10-CM crosswalk table for each code. | Excel list of CPT codes | `cpt_icd10_detailed_data*.xlsx` |

While a scraper runs, it writes incremental CSVs, `checkpoint.json` and failure screenshots to `./outputs/` (git-ignored). The finished, cleaned results are what you see in `data/`.

## Dataset

Record counts are for the code year 2025.

### `data/cpt/`: description, guidelines, lay terms

| Folder | Section | Records |
|---|---|---:|
| `anesthesia_00100-01999` | Anesthesia | 276 |
| `surgery_10004-69990` | Surgery (`cpt_surgery_1xxxx` … `6xxxx`) | 4,877 |
| `radiology_70010-79999` | Radiology | 661 |
| `pathology_lab_80047-89398` | Pathology & Laboratory | 1,646 |
| `medicine_90281-99607` | Medicine | 1,027 |
| `evaluation_management_98000-99499` | Evaluation & Management | 147 |
| `category_ii_0001F-9007F` | Category II (performance measures) | 565 |
| `category_iii_0042T-0987T` | Category III (emerging technology) | 543 |
| `pla_0001U-0599U` | Proprietary Laboratory Analyses | 537 |
| `maaa_0002M-0020M` | Multianalyte Assays with Algorithmic Analyses | 15 |
| `layterm_backfill` | Lay terms for codes missed in range runs (`_raw` = 2,046, `_fixed` = 1,155) | 2,046 |

**Columns:** `code, short_description, lay_term, guidelines, code_range, source_url, scrape_timestamp`
(`layterm_backfill`: `code, lay_term, source_url, timestamp`)

### `data/hcpcs/`

| Folder | Contents | Records |
|---|---|---:|
| `j_series` | Drugs administered other than oral method | 1,039 |
| `q_series` | Temporary codes | 513 |
| `chemo_non_chemo_rules` | HCPCS drug rule workbooks (see below) | — |

**`chemo_non_chemo_rules/`**
- `hcpcs_chemo_non_chemo_rules.xlsx`: HCPCS drug codes with drug name, brand name, billing unit descriptor, **Chemo vs Non-Chemo** classification and **JW / JZ** modifier applicability. Sheets: *HCPCS Codes*, *Non-Injectable & Other Routes*, *Removed - Terminated*.
- `hcpcs_chemo_non_chemo_rules_2026-10-14.xlsx`: updated revision (14 Oct 2026) with full long descriptions in place of brand names.

### `data/modifiers/`

| Folder | Records | Columns |
|---|---:|---|
| `cpt_modifiers` | 49 | `url, range, timestamp, code, description, lay_term` |
| `hcpcs_modifiers` | 373 | `code, short_description, lay_term, guidelines, code_range, source_url, scrape_timestamp` |
| `cpt_modifier_x` | 149,789 | `cpt_code, modifier, description, allowed_entity` |

`cpt_modifier_x/` is split by the first digit of the CPT code (`cpt_modifier_x_0_series.csv` … `cpt_modifier_x_9_series.csv`). `cpt_modifier_x_master.csv` contains all of them.

```csv
cpt_code,modifier,description,allowed_entity
00100,23,Unusual Anesthesia,P
00100,53,Discontinued Procedure,P
```

### `data/icd10/`

- `icd10_excludes1.xlsx`: ICD-10-CM codes and categories mapped to all of their **Excludes1** codes (`Main Code | Main Description | All Excludes1 Codes`). For example, `A04- Other bacterial intestinal infections → A05.-, A18.32`.

## Setup

**Requirements:** Python 3.9+, Google Chrome, a matching ChromeDriver, and an AAPC Codify account.

```bash
git clone https://github.com/<your-username>/aapc-medical-coding-scraper.git
cd aapc-medical-coding-scraper
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

1. **ChromeDriver:** download the version that matches your Chrome from [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/). Extract it to `./chromedriver/chromedriver-win64/chromedriver.exe` (git-ignored), or edit `CHROMEDRIVER_PATH` at the top of each script.
2. **Credentials:** the scripts ship with dummy values. Replace `YOUR_AAPC_USERNAME` / `YOUR_AAPC_PASSWORD` in the script you run (`cpt_layterm_backfill.py` takes `--username` / `--password` flags instead). **Never commit real credentials.**

## Usage

Run every command from the repository root.

```bash
# CPT range: description, guidelines, lay term
python scrapers/cpt_range_scraper.py --start-code 80047 --stop-code 89398 --no-headless --log-level DEBUG

# CPT modifiers list
python scrapers/cpt_range_scraper.py --mode modifiers

# Modifier X crosswalk for a CPT range
python scrapers/cpt_modifier_x_scraper.py --start-code 00100 --stop-code 01999

# Modifier X crosswalk for codes listed in cpt_codes.xlsx (column CPT_CODE)
python scrapers/cpt_list_scraper.py

# Back-fill lay terms for a list of codes
python scrapers/cpt_layterm_backfill.py --input-file missing_codes.xlsx --cpt-column CPT_Code --output-file layterm_results.xlsx

# HCPCS J series
python scrapers/hcpcs_code_scraper.py --code-type HCPCS --start-code J0120 --stop-code J9999

# HCPCS modifiers
python scrapers/hcpcs_modifier_scraper.py --code-type HCPCS --start-code A1 --stop-code ZC
```

### Common flags

| Flag | Default | Description |
|---|---|---|
| `--headless` / `--no-headless` | headless | Run Chrome with or without a visible window |
| `--year` | `2025` | Code-set year selected in Codify |
| `--output-dir` | `./outputs` | Where incremental CSVs, checkpoints and screenshots go |
| `--throttle` | `0.5` | Base delay in seconds between actions (randomised) |
| `--max-retries` | `3` | Login / page retries |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, … |

## How the scrapers work

1. **Login:** opens aapc.com, signs in, dismisses pop-ups and the terms-migration modal, and opens **Codify**.
2. **Year selection:** selects the code-set year from the dropdown.
3. **Search → scrape → next loop:** searches for the start code, opens its detail page, and expands the *Details*, *Guidelines* and *Lay Term* sections. Clicking **Read more** reveals the full lay term. The scraper then follows the breadcrumb **Next** arrow to the next code until it reaches the stop code.
4. **Checkpointing:** after every code, it appends a row to the incremental CSV and updates `checkpoint.json`, so an interrupted run resumes where it left off.
5. **Resilience:** explicit waits, fallback XPaths, retries, randomised throttling, and screenshots of failed pages in `outputs/artifacts/`. Failed codes are logged to a `failed_*.csv` file for re-runs.
6. **Post-processing:** the incremental CSVs are cleaned, de-duplicated and saved as CSV + XLSX under `data/`.

**Tech stack:** Python · Selenium 4 · pandas · openpyxl

## Disclaimer

This project is for **educational and research purposes**.

- CPT® is a registered trademark of the American Medical Association (AMA). CPT descriptions are © AMA. AAPC Codify content, including lay terms and guidelines, is proprietary to AAPC.
- HCPCS Level II and ICD-10-CM code sets are published by CMS / CDC.
- Use these scripts only with your own licensed AAPC account and in line with AAPC's Terms of Use. You are responsible for how you use and redistribute the scraped data.

---

Made by **Atharva Kanase**
