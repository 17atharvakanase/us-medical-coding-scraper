from __future__ import annotations
import argparse
import json
import logging
import os
import random
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    StaleElementReferenceException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import pandas as pd
import csv
import tempfile

# ---------------- Config defaults ----------------
DEFAULT_YEAR = "2025"
DEFAULT_HEADLESS = True
DEFAULT_MAX_RETRIES = 3
DEFAULT_THROTTLE = 0.5
# INCREMENTAL_CSV = "./outputs/cpt_scraped_incremental.csv"
# INCREMENTAL_CSV = "./outputs/cpt_modifier_x_crosswalk.csv"

SEARCH_INPUT_ID = "top_search"
DETAILS_LINK_TEXT = "Details"
GUIDELINES_LINK_TEXT = "Guidelines"
LAYTERM_LINK_TEXT = "Lay Term"
FULL_LAYTERM_ID = "fullLayterm"
DESCRIPTION_COLLAPSE_XPATH = "//div[@id='description_info']/div/h4/span"
COLLAPSE_DESC_INFO_XPATH = "//div[@id='collapse_desc_info']/div/div"
BREADCRUMB_NEXT_ICON_XPATH = "//div[@id='breadcrumbs']/div/div[2]/div/a[2]/div/i"

CHROMEDRIVER_PATH =(r".\chromedriver\chromedriver-win64\chromedriver.exe")

# FAILED_MODIFIER_X = []


FAILED_CSV_PATH = Path("./outputs/failed_modifier_x_codes.csv")


# ---------------- Logging ----------------
logger = logging.getLogger("aapc_search_loop")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_console)

# ---------------- Utilities ----------------
def ensure_dirs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts" / "screenshots").mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts" / "failures").mkdir(parents=True, exist_ok=True)

def now_ts_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def save_screenshot(driver, path: Path) -> None:
    try:
        driver.save_screenshot(str(path))
        logger.debug("Saved screenshot %s", path)
    except Exception as e:
        logger.warning("Failed saving screenshot %s: %s", path, e)

def save_html_dump(driver, path: Path) -> None:
    try:
        path.write_text(driver.page_source, encoding="utf-8")
        logger.debug("Saved HTML dump %s", path)
    except Exception as e:
        logger.warning("Failed saving HTML dump %s: %s", path, e)

def random_throttle(base: float) -> None:
    time.sleep(base + random.random() * base)

# ---------------- Selenium driver builer------------
def build_driver(headless: bool) -> webdriver.Chrome:
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    service = Service(CHROMEDRIVER_PATH)

    # quiet noisy loggers
    logging.getLogger("selenium").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)

    old_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        driver = webdriver.Chrome(service=service, options=opts)
    finally:
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.stderr = old_stderr

    driver.set_page_load_timeout(60)
    return driver

def wait_for(driver, by, locator, timeout: int = 20, condition: str = "presence"):
    cond_map = {
        "clickable": EC.element_to_be_clickable((by, locator)),
        "visible": EC.visibility_of_element_located((by, locator)),
        "presence": EC.presence_of_element_located((by, locator)),
    }
    try:
        return WebDriverWait(driver, timeout).until(cond_map[condition])
    except TimeoutException:
        raise

def wait_for_optional(driver, by, locator, timeout: int = 5, condition: str = "presence"):
    try:
        return wait_for(driver, by, locator, timeout=timeout, condition=condition)
    except TimeoutException:
        return None
    except Exception:
        return None

def safe_find_elements(driver, by, locator) -> List:
    try:
        return driver.find_elements(by, locator)
    except Exception:
        return []

def dismiss_popups(driver) -> None:
    try:
        candidates = [
            ("css selector", 'button[aria-label="Close"]'),
            ("css selector", ".modal .close"),
            ("xpath", "//button[contains(@class,'close') and (contains(@aria-label,'Close') or contains(text(),'Close'))]"),
            ("xpath", "//button[contains(text(),'Skip')]"),
            ("xpath", "//button[contains(text(),'×')]"),
            ("css selector", "button.btn.btn-primary.skip-btn"),
        ]
        for by, sel in candidates:
            try:
                if by == "xpath":
                    els = driver.find_elements(By.XPATH, sel)
                else:
                    els = driver.find_elements(getattr(By, by.upper()), sel)
                for el in els:
                    if el.is_displayed():
                        try:
                            el.click()
                            logger.info("Clicked popup close/skip")
                            time.sleep(0.4)
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", el)
                            except Exception:
                                pass
            except Exception:
                continue
    except Exception as e:
        logger.debug("dismiss_popups failed: %s", e)

def login_to_aapc(driver, username: str, password: str, allow_placeholder: bool, max_retries: int = 3) -> bool:
    if (username.startswith("PLACEHOLDER") or password.startswith("PLACEHOLDER")) and not allow_placeholder:
        logger.warning("Credentials are placeholder values and --allow-placeholder not set. Exiting.")
        return False

    login_url = "https://www.aapc.com/"
    try:
        driver.get(login_url)
    except WebDriverException:
        logger.exception("Failed to open aapc.com login page")
        return False

    try:
        # Try click Sign In (several fallbacks)
        try:
            sign_in = wait_for(driver, By.ID, "btnSignIn", timeout=10, condition="clickable")
            sign_in.click()
        except TimeoutException:
            try:
                link = wait_for(driver, By.XPATH, "//a[contains(text(),'Sign In')]", timeout=5, condition="clickable")
                link.click()
            except Exception:
                logger.exception("Sign In element not found/clickable")
                return False

        # Enter username and continue
        try:
            user_el = wait_for(driver, By.ID, "userProvidedSignInName", timeout=20, condition="visible")
            user_el.clear()
            user_el.send_keys(username)
            next_btn = wait_for(driver, By.ID, "next", timeout=10, condition="clickable")
            next_btn.click()
        except TimeoutException:
            logger.exception("Username field or continue button not found during login.")
            return False

        # Enter password and finish
        try:
            pw_el = wait_for(driver, By.ID, "password", timeout=15, condition="visible")
            pw_el.clear()
            pw_el.send_keys(password)
            cont = wait_for(driver, By.ID, "continue", timeout=10, condition="clickable")
            cont.click()
        except TimeoutException:
            logger.exception("Password field or continue button not found during login.")
            return False

        # Confirm landing on Codify/dashboard
        try:
            wait_for(driver, By.XPATH, "//a[contains(@href,'/codes/all_coding_tools/home')]", timeout=25, condition="presence")
            logger.info("Login seems successful (codify link present).")
            return True
        except TimeoutException:
            # Some accounts land on dashboard—try that path
            try:
                wait_for(driver, By.XPATH, "//a[contains(@href,'/account/dashboard.aspx')]", timeout=10)
                logger.info("Login landed on dashboard; attempting Codify menu.")
                try:
                    codify_menu = wait_for(driver, By.XPATH, "//li[@id='ctl00_preTopMegaMenu_coderPretop']/a", timeout=10, condition="clickable")
                    codify_menu.click()
                    wait_for(driver, By.XPATH, "//form[@id='migrate_user_terms']/div[2]/button[2]", timeout=10)
                    logger.info("Codify entry point detected.")
                    return True
                except Exception:
                    logger.warning("Codify menu click failed after dashboard load.")
                    return True
            except Exception:
                logger.error("Login did not reach dashboard or codify home. There may be an MFA/extra step.")
                return False

    except Exception as e:
        logger.exception("Login error: %s", e)
        return False

def ensure_year_selected(driver, year: str) -> None:
    try:
        sel_elem = None
        try:
            sel_elem = wait_for(driver, By.XPATH, "(.//*[normalize-space(text()) and normalize-space(.)='Code Set Year :'])[1]/following::select[1]", timeout=8, condition="presence")
        except TimeoutException:
            try:
                sel_elem = driver.find_element(By.CSS_SELECTOR, "select")
            except Exception:
                sel_elem = None

        if sel_elem is None:
            logger.debug("No year select found; continuing.")
            return

        try:
            select = Select(sel_elem)  # type: ignore[arg-type]
            found = False
            for opt in select.options:
                val = opt.get_attribute("value")
                if val == str(year) or opt.text.strip().startswith(str(year)):
                    try:
                        select.select_by_value(val)
                    except Exception:
                        try:
                            select.select_by_index(select.options.index(opt))
                        except Exception:
                            pass
                    found = True
                    logger.info("Selected code set year: %s", opt.text)
                    time.sleep(0.5)
                    break
            if not found:
                try:
                    select.select_by_index(0)
                    logger.warning("Requested year not found; defaulted to first option.")
                except Exception:
                    logger.warning("Select exists but couldn't change selection.")
        except Exception:
            try:
                sel_elem.click()
                opt = wait_for(driver, By.XPATH, f"//li[contains(text(),'{year}') or contains(text(),'{str(year)[-2:]}')]", timeout=5)
                opt.click()
                logger.info("Selected year using click-based fallback.")
            except Exception:
                logger.warning("Could not select year via fallback click.")
    except Exception as e:
        logger.warning("ensure_year_selected error: %s", e)

# def append_rows(rows: List[Dict], fieldnames: Optional[List[str]] = None):
#     if not rows:
#         return
#     os.makedirs(os.path.dirname(INCREMENTAL_CSV), exist_ok=True)
#     exists = os.path.exists(INCREMENTAL_CSV)
#     if fieldnames is None:
#         fieldnames = list(rows[0].keys())
#     with open(INCREMENTAL_CSV, "a", newline="", encoding="utf-8") as f:
#         writer = csv.DictWriter(f, fieldnames=fieldnames)
#         if not exists:
#             writer.writeheader()
#         for r in rows:
#             writer.writerow({k: (r.get(k) or "") for k in fieldnames})

def append_rows(rows, fieldnames, csv_path):
    if not rows:
        return

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def atomic_write_json(path: Path, data: dict):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def immediate_checkpoint_append(checkpoint_path: Path, record: dict):
    try:
        if checkpoint_path.exists():
            cp = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        else:
            cp = {"records": [], "last_scraped": None}
        cp.setdefault("records", []).append(record)
        cp["last_scraped"] = record.get("code") or record.get("source_url")
        atomic_write_json(checkpoint_path, cp)
        logger.debug("Immediate checkpoint updated: %s", cp["last_scraped"])
    except Exception as e:
        logger.exception("Failed immediate_checkpoint_append: %s", e)


def _expand_read_more_in(el):
    try:
        buttons = el.find_elements(By.XPATH, ".//a[contains(.,'Read More') or contains(.,'Read more') or contains(.,'... Read More') or contains(.,'Read More ›') or contains(.,'... Read more')]")
        for b in buttons:
            try:
                b.click()
                time.sleep(0.25)
            except Exception:
                try:
                    el._parent.execute_script("arguments[0].click();", b)
                except Exception:
                    pass
    except Exception:
        pass

def go_to_codify_home(driver):
    logger.warning("Resetting to Codify home")
    driver.get("https://www.aapc.com/codes/all_coding_tools/home")
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.ID, SEARCH_INPUT_ID))
    )
    time.sleep(1)

def get_series_csv_path(cpt_code: str, output_dir: Path) -> Path:
    """
    Returns output CSV path based on first digit of CPT code.
    Example: 00100 -> 0-series CSV
    """
    first_digit = cpt_code[0] if cpt_code and cpt_code[0].isdigit() else "other"
    return output_dir / f"cpt_modifier_x_{first_digit}_series.csv"

# def group_modifiers_with_description(records):
#     grouped = []
#     for r in records:
#         grouped.append(
#             f"{r['modifier']}||{r['description']}||{r['allowed_entity']}"
#         )
#     return " ;; ".join(grouped)

#$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$

# def record_failed_cpt(cpt_code: str, reason: str):
#     logger.warning("Recording failed CPT %s | Reason: %s", cpt_code, reason)
#     FAILED_MODIFIER_X.append({
#         "CPT_CODE": cpt_code,
#         "REASON": reason,
#         "TIMESTAMP": now_ts_iso()
#     })

def record_failed_cpt(cpt_code: str, reason: str):
    logger.warning("Recording failed CPT %s | Reason: %s", cpt_code, reason)

    row = {
        "CPT_CODE": cpt_code,
        "REASON": reason,
        "TIMESTAMP": now_ts_iso()
    }

    # keep in memory (optional)
    # FAILED_MODIFIER_X.append(row)

    # 🔒 write immediately to CSV (crash-safe)
    FAILED_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = FAILED_CSV_PATH.exists()

    with open(FAILED_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["CPT_CODE", "REASON", "TIMESTAMP"]
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
# 1111111111111111111111111111111111111111111111111111111111111111111111111111111111111
# def force_cpt_tool(driver):
#     if not is_wrong_tool_context(driver):
#         return  # already safe

#     logger.warning("Wrong tool context detected, switching back to CPT")

#     driver.get("https://www.aapc.com/codes/all_coding_tools/home")

#     cpt_link = WebDriverWait(driver, 15).until(
#         EC.element_to_be_clickable(
#             (By.XPATH, "//a[.//text()[normalize-space()='CPT']]")
#         )
#     )

#     driver.execute_script("arguments[0].click();", cpt_link)
#     time.sleep(1.2)

# def is_wrong_tool_context(driver):
#     return any(x in driver.current_url for x in [
#         "/mcr-ncci",
#         "/ncci",
#         "/mcr",
#         "/icd",
#         "/hcpcs"
#     ])

# 111111111111111111111111111111111111111111111111111111111111111111111111111111111111111
def click_modifier_x_tab(driver, cpt_code: str) -> bool:
    try:
        modal_url = (
            f"https://www.aapc.com/codes/modifier_crosswalk/"
            f"modifier_crosswalk_updated/{cpt_code}"
        )

        driver.execute_script(
            "show_content_modal(arguments[0], arguments[1], 'GET', 1);",
            f"Modifiers Crosswalk for {cpt_code}",
            modal_url
        )

        WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.ID, "messageModal_title"))
        )

        logger.info("Opened Modifier X modal via JS for %s", cpt_code)
        return True

    except Exception as e:
        logger.error("Modifier X JS open failed for %s: %s", cpt_code, e)
        return False

def map_color_to_entity(color):
    if not color:
        return "P"

    color = color.lower()
    if "234, 85, 1" in color or "#ea5501" in color:
        return "ASC"
    if "0, 105, 255" in color or "#0069ff" in color:
        return "ASC & P"
    return "P"

def scrape_modifier_x_modal(driver, cpt_code):
    rows_data = []

    try:
        # 🔒 1. VERIFY MODAL TITLE MATCHES CPT
        title_el = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "messageModal_title"))
        )
        modal_title = title_el.text.strip()

        if cpt_code not in modal_title:
            logger.warning(
                "Modifier X modal title mismatch. Expected %s, got '%s'",
                cpt_code,
                modal_title
            )
            return []   # ⛔ DO NOT SCRAPE WRONG DATA

        # 🔒 2. WAIT FOR TABLE ROWS
        rows = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//tbody[contains(@class,'points_table_scrollbar')]/tr")
            )
        )

        # 🔒 3. SCRAPE ROWS
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 2:
                continue

            modifier = tds[0].text.strip()
            description = tds[1].text.strip()

            color = tds[1].value_of_css_property("color")
            entity = map_color_to_entity(color)

            rows_data.append({
                "cpt_code": cpt_code,
                "modifier": modifier,
                "description": description,
                "allowed_entity": entity
            })

    except Exception as e:
        logger.error(
            "Error scraping Modifier X modal for %s: %s",
            cpt_code,
            e
        )

    return rows_data

def close_modifier_x_modal(driver):
    try:
        close_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "messageModal_close"))
        )
        driver.execute_script("arguments[0].click();", close_btn)

        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.ID, "messageModal_title"))
        )
    except Exception as e:
        logger.warning("Modal close issue: %s", e)

# def search_code_only(driver, code_token: str):
#     logger.info("Searching CPT (no details) %s", code_token)
#     dismiss_popups(driver)

#     search = wait_for(driver, By.ID, SEARCH_INPUT_ID, timeout=10, condition="clickable")
#     driver.execute_script("arguments[0].click();", search)
#     search.clear()
#     search.send_keys(code_token)
#     search.send_keys(Keys.ENTER)

#     random_throttle(DEFAULT_THROTTLE)
#     dismiss_popups(driver)
#     time.sleep(1.0)
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

def search_code_only(driver, code_token: str):
    logger.info("Searching CPT (no details) %s", code_token)
    dismiss_popups(driver)

    for attempt in range(3):
        try:
            search = wait_for(driver, By.ID, SEARCH_INPUT_ID, timeout=15, condition="presence")

            driver.execute_script("arguments[0].scrollIntoView(true);", search)
            time.sleep(0.3)

            search.click()
            time.sleep(0.2)

            search.clear()
            time.sleep(0.2)

            search.send_keys(code_token)
            search.send_keys(Keys.ENTER)

            random_throttle(DEFAULT_THROTTLE)
            dismiss_popups(driver)
            time.sleep(1.0)
            return  # ✅ success

        except (StaleElementReferenceException, TimeoutException):
            logger.warning("Search input unstable, retrying (%d/3)", attempt + 1)
            time.sleep(1)
            go_to_codify_home(driver)

    #  If all retries fail
    raise TimeoutException("Search input not recoverable")

# def activate_cpt_row(driver, cpt_code: str) -> bool:
#     """
#     Activates CPT context WITHOUT navigating to Details.
#     """
#     try:
#         cpt_link = WebDriverWait(driver, 10).until(
#             EC.element_to_be_clickable(
#                 (By.XPATH, f"//a[normalize-space(text())='{cpt_code}']")
#             )
#         )

#         driver.execute_script(
#             "arguments[0].scrollIntoView({block:'center'});",
#             cpt_link
#         )
#         time.sleep(0.3)

#         # Click CPT link once to set context
#         driver.execute_script("arguments[0].click();", cpt_link)
#         time.sleep(0.5)

#         return True

#     except Exception as e:
#         logger.warning("Failed to activate CPT %s: %s", cpt_code, e)
#         return False


# £££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££££

def scrape_code_page(driver, code_url: str, code_range: str) -> Dict[str, Optional[str]]:
    data = {
        "code": None,
        "short_description": None,
        "lay_term": None,
        "guidelines": None,
        "code_range": code_range,
        "source_url": code_url,
        "scrape_timestamp": now_ts_iso(),
    }

    try:
        # ensure on the page
        try:
            driver.get(code_url)
        except Exception:
            logger.debug("driver.get failed for url; will try to continue with current page")

        random_throttle(DEFAULT_THROTTLE)
        dismiss_popups(driver)

        # --- CODE extraction
        try:
            code_from_url = code_url.rstrip("/").split("/")[-1]
            header_el = wait_for_optional(driver, By.XPATH, "//h1[contains(@class,'pull-left') or contains(@class,'page-title')]", timeout=6, condition="visible")
            header = header_el.text.strip() if header_el else ""
            code_regex = re.compile(r"\b\d{3,5}[A-Z]{0,2}\b")
            found = None
            if header:
                m = code_regex.search(header)
                if m:
                    found = m.group(0)
            if not found:
                m2 = code_regex.search(code_from_url)
                if m2:
                    found = m2.group(0)
            data["code"] = found or code_from_url
        except Exception:
            data["code"] = code_url.rstrip("/").split("/")[-1]

        # --- SHORT DESCRIPTION
        try:
            desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info']//div[contains(@class,'panel-body') or contains(@class,'description')]", timeout=6, condition="visible")
            if not desc_el:
                desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info' or contains(@class,'cpt-description')]", timeout=4, condition="presence")
            if desc_el:
                try:
                    # click expand
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", desc_el)
                    except Exception:
                        pass
                    _expand_read_more_in(desc_el)
                except Exception:
                    pass
                data["short_description"] = " ".join(desc_el.text.split())
            else:
                p_el = wait_for_optional(driver, By.XPATH, "//div[contains(@class,'cpt-description')]//p", timeout=3, condition="presence")
                data["short_description"] = p_el.text.strip() if p_el else ""
        except Exception as e:
            logger.debug("short_description extraction exception: %s", e)
            data["short_description"] = ""

        # # --- GUIDELINES ---
        try:
            data["guidelines"] = ""
            # click Guidelines tab if present
            try:
                tab = wait_for_optional(driver, By.LINK_TEXT, GUIDELINES_LINK_TEXT, timeout=4, condition="clickable")
                if tab:
                    try:
                        driver.execute_script("arguments[0].click();", tab)
                    except Exception:
                        tab.click()
                    time.sleep(1.0)
            except Exception:
                pass
                
            # find panel
            panel = wait_for_optional(driver, By.XPATH, "//*[@id='cpt_guidelines']", timeout=5, condition="visible")
            if panel:
                try:
                    _expand_read_more_in(panel)
                except Exception:
                    pass
                txt = " ".join(panel.text.split()).strip()
                if txt and len(txt) > 40 and "Upcoming & Historical Information" not in txt:
                    data["guidelines"] = txt

            if not data["guidelines"]:
                paras = safe_find_elements(driver, By.XPATH, "//div[@id='cpt_guidelines']//p | //section[contains(@class,'guidelines')]//p")
                for p in paras:
                    t = " ".join(p.text.split()).strip()
                    if len(t) > 40:
                        data["guidelines"] = t
                        break

            if not data["guidelines"]:
                try:
                    link = driver.find_element(By.XPATH, "//a[contains(.,'Guidelines') or contains(.,'Surgery Guidelines')]")
                    data["guidelines"] = f"{link.text.strip()} | {link.get_attribute('href') or ''}"
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Guidelines extraction err: %s", e)
            data["guidelines"] = data.get("guidelines", "")

        # --- LAY TERM ---
        
        try:
            data["lay_term"] = ""
            try:
                lay_tab = wait_for_optional(driver, By.LINK_TEXT, LAYTERM_LINK_TEXT, timeout=3, condition="clickable")
                if lay_tab:
                    try:
                        driver.execute_script("arguments[0].click();", lay_tab)
                    except Exception:
                        lay_tab.click()
                    time.sleep(1)
            except Exception:
                pass

            lay_selectors = [
                "//*[@id='cpt_layterm']//div[contains(@class,'panel-body') or normalize-space(text())]",
                # "/html/body/div[6]/section/div/div/div[2]/div/div/div[3]/div[2]/div[2]/div/div[2]/div/div/div[2]/div[2]/div[4]/div[2]",
                "//*[@id='cpt_layterm' or contains(@class,'cpt-layterm') or contains(@class,'layterm')]//div[contains(@class,'panel-body') or p or div]",
                "//a[contains(normalize-space(.),'Lay Term')]/ancestor::li/following-sibling::*[1]//div[contains(@class,'panel-body') or normalize-space(text())]",
            ]
            lay_txt = ""
            for sel in lay_selectors:
                try:
                    lay_el = wait_for_optional(driver, By.XPATH, sel, timeout=3, condition="visible")
                    if lay_el:
                        try:
                            _expand_read_more_in(lay_el)
                        except Exception:
                            pass
                        txt = " ".join(lay_el.text.split()).strip()
                        if txt and txt.lower() != "upcoming & historical information":
                            lay_txt = txt
                            break
                except Exception:
                    continue

            if not lay_txt:
                try:
                    candidates = driver.find_elements(By.XPATH, "//*[contains(translate(., 'LAY TERM', 'lay term'), 'lay term') or contains(translate(., 'Lay Term', 'lay term'), 'lay term')]/following::p[1] | //*[contains(@class,'layterm') or contains(@id,'layterm')]")
                    for c in candidates:
                        try:
                            txt = " ".join(c.text.split()).strip()
                            if txt and txt.lower() != "upcoming & historical information":
                                lay_txt = txt
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

            data["lay_term"] = lay_txt or ""
        except Exception as e:
            logger.debug("Lay term extraction exception: %s", e)
            data["lay_term"] = ""
        #   --- LAY TERM (improved, retry + robust selectors) ---
        
#--------------------------------------------------------------------------------
        # normalize missing strings
        for k in ("short_description", "lay_term", "guidelines"):
            if data.get(k) is None:
                data[k] = ""

        logger.info("Scraped code page: %s (url: %s)", data.get("code"), code_url)
    except Exception as e:
        logger.exception("scrape_code_page failed for %s: %s", code_url, e)

    return data

# ---------------- Search + Details opener + Next click ----------------
def search_and_open_code(driver, code_token: str) -> Optional[str]:
    logger.info("Searching CPT %s", code_token)
    try:
        dismiss_popups(driver)
        search = wait_for(driver, By.ID, SEARCH_INPUT_ID, timeout=10, condition="clickable")
        try:
            driver.execute_script("arguments[0].click();", search)
        except Exception:
            search.click()
        search.clear()
        search.send_keys(code_token)
        search.send_keys(Keys.ENTER)
        random_throttle(DEFAULT_THROTTLE)
        dismiss_popups(driver)
        time.sleep(1.0)

        cur = driver.current_url
        # if landed on a range page try Details
        if "/cpt-codes-range/" in cur or "/codes/cpt-codes-range/" in cur:
            logger.debug("Search landed on range page: %s", cur)
            det = wait_for_optional(driver, By.LINK_TEXT, DETAILS_LINK_TEXT, timeout=5, condition="clickable")
            if det:
                try:
                    driver.execute_script("arguments[0].click();", det)
                except Exception:
                    try:
                        det.click()
                    except Exception:
                        pass
                random_throttle(DEFAULT_THROTTLE)
                dismiss_popups(driver)
                time.sleep(0.6)
                logger.debug("Clicked Details link after search.")
        # wait for expected header
        try:
            wait_for(driver, By.XPATH, "//h1[contains(@class,'pull-left') or contains(@class,'page-title')]", timeout=8, condition="visible")
        except Exception:
            logger.debug("Code header not found after search; continuing anyway.")
        return driver.current_url
    except Exception:
        logger.exception("search_and_open_code failed for %s", code_token)
        return None

def click_next_code(driver) -> bool:
    try:
        cur = driver.current_url
        # primary: breadcrumb icon (the <i> inside <a>)
        try:
            nxt_icon = wait_for_optional(driver, By.XPATH, BREADCRUMB_NEXT_ICON_XPATH, timeout=3, condition="clickable")
            if nxt_icon:
                try:
                    parent_a = nxt_icon.find_element(By.XPATH, "ancestor::a[1]")
                    driver.execute_script("arguments[0].click();", parent_a)
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", nxt_icon)
                    except Exception:
                        nxt_icon.click()
                random_throttle(DEFAULT_THROTTLE)
                dismiss_popups(driver)
                try:
                    WebDriverWait(driver, 6).until(lambda d: d.current_url != cur)
                except Exception:
                    pass
                return driver.current_url != cur
        except Exception:
            pass

        # fallback: find an anchor with 'Next' text or > icon
        try:
            fallback = wait_for_optional(driver, By.XPATH, "//a[contains(normalize-space(.),'Next') or normalize-space(.)='>' or normalize-space(.)='>>' or contains(@aria-label,'next')]", timeout=3, condition="clickable")
            if fallback:
                try:
                    driver.execute_script("arguments[0].click();", fallback)
                except Exception:
                    fallback.click()
                random_throttle(DEFAULT_THROTTLE)
                dismiss_popups(driver)
                try:
                    WebDriverWait(driver, 6).until(lambda d: d.current_url != cur)
                except Exception:
                    pass
                return driver.current_url != cur
        except Exception:
            pass

        logger.info("No next control found or clickable.")
        return False
    except Exception as e:
        logger.exception("click_next_code error: %s", e)
        return False

# ---------------- Main start->stop loop ----------------
def run_search_to_stop_loop(driver, start_code: str, stop_code: str, checkpoint_path: Path, output_dir: Path, throttle: float = DEFAULT_THROTTLE, max_cycles: int = 2000):
    logger.info("run_search_to_stop_loop start=%s stop=%s", start_code, stop_code)
    try:
        first_url = search_and_open_code(driver, start_code)
        if not first_url:
            logger.error("Could not open start code %s", start_code)
            return

        cycles = 0
        prev_page_marker = None

        while cycles < max_cycles:
            cycles += 1
            try:
                cur_url = driver.current_url
                rec = scrape_code_page(driver, cur_url, code_range=start_code)
                rec["scrape_timestamp"] = now_ts_iso()
                rec["source_url"] = cur_url

                # save immediate: CSV + checkpoint
                append_rows([rec], fieldnames=list(rec.keys()))
                immediate_checkpoint_append(checkpoint_path, rec)
                logger.info("Scraped & saved: %s (cycle %d)", rec.get("code"), cycles)

                # stop condition
                code_token = (rec.get("code") or "").strip()
                if stop_code and code_token and code_token == stop_code:
                    logger.info("Reached stop code %s — stopping loop.", stop_code)
                    break

                # try next
                moved = click_next_code(driver)
                if not moved:
                    logger.info("Couldn't move to next code after %s — stopping.", rec.get("code"))
                    break

                random_throttle(throttle)

                # detect same page loop
                try:
                    new_header_el = wait_for_optional(driver, By.XPATH, "//h1[contains(@class,'pull-left') or contains(@class,'page-title')]", timeout=6, condition="visible")
                    new_marker = new_header_el.text.strip() if new_header_el else driver.current_url
                except Exception:
                    new_marker = driver.current_url
                if new_marker == prev_page_marker:
                    logger.warning("Next navigation resulted in same page — stopping to avoid loop.")
                    break
                prev_page_marker = new_marker

            except Exception as e:
                logger.exception("Error inside search_to_stop loop: %s", e)
                try:
                    ts = int(time.time())
                    ss = output_dir / "artifacts" / "screenshots" / f"error_cycle_{ts}.png"
                    dump = output_dir / "artifacts" / "failures" / f"error_cycle_{ts}.html"
                    save_screenshot(driver, ss)
                    save_html_dump(driver, dump)
                    logger.warning("Saved debug artifacts for error cycle: %s %s", ss, dump)
                except Exception:
                    pass
                break

        logger.info("run_search_to_stop_loop finished after %d cycles", cycles)
    except Exception:
        logger.exception("run_search_to_stop_loop fatal error")

# -MAIN
def main():
    parser = argparse.ArgumentParser(description="AAPC Codify CPT search->scrape->next scraper")
    parser.add_argument("--headless", action="store_true", default=DEFAULT_HEADLESS, help="Run Chrome headless (default True)")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run with UI")
    parser.add_argument("--year", default=DEFAULT_YEAR)
    parser.add_argument("--output-dir", default="./outputs")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE)
    parser.add_argument("--allow-placeholder", action="store_true", help="Allow placeholder credentials")
    # parser.add_argument("--start-code", type=str, required=True, help="Start CPT code to search and begin scraping from")
    # parser.add_argument("--stop-code", type=str, default="", help="Stop CPT code (inclusive); leave empty to run until navigation stops")
    args = parser.parse_args()

    logger.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))
    output_dir = Path(args.output_dir)
    ensure_dirs(output_dir)

    username = "YOUR_AAPC_USERNAME"
    password = "YOUR_AAPC_PASSWORD"
    logger.warning("Using hardcoded credentials (change to env/secret in production)")

    checkpoint_path = output_dir / "checkpoint.json"

    driver = build_driver(args.headless)
    logger.info("Driver created (headless=%s)", args.headless)

    try:
        logged = login_to_aapc(driver, username, password, allow_placeholder=args.allow_placeholder, max_retries=args.max_retries)
        if not logged:
            logger.error("Login failed. Exiting.")
            try:
                driver.quit()
            except Exception:
                pass
            sys.exit(2)


        try:
            skip_btn = wait_for_optional(driver, By.XPATH, "//form[@id='migrate_user_terms']/div[2]/button[2]", timeout=5, condition="clickable")
            if skip_btn:
                try:
                    skip_btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", skip_btn)
                logger.info("Clicked Skip on migrate_user_terms modal.")
        except Exception:
            pass

        ensure_year_selected(driver, args.year)
        df = pd.read_excel("cpt_codes.xlsx")
        for _, row in df.iterrows():
            raw_code = str(row["CPT_CODE"]).strip()

            # Normalize CPT to 5 digits if numeric
            if raw_code.isdigit():
                cpt_code = raw_code.zfill(5)
            else:
                cpt_code = raw_code

            if not cpt_code:
                continue
            
# 1111111111111111111111111111111111111111
            # force_cpt_tool(driver)
# 111111111111111111111111111111111111111111
            #  Enforce year before every search
            ensure_year_selected(driver, args.year)

            # search_and_open_code(driver, cpt_code)
            # Search CPT
            # HARD RESET to avoid stale CPT context
            go_to_codify_home(driver)

            # Search CPT
            try:
                search_code_only(driver, cpt_code)
            except TimeoutException:
                record_failed_cpt(cpt_code, "Search input not found (page not ready)")
                go_to_codify_home(driver)
                continue


            # Activate CPT row (do NOT open Details)
            # if not activate_cpt_row(driver, cpt_code):
            #     record_failed_cpt(cpt_code, "Failed to activate CPT row")
            #     go_to_codify_home(driver)
            #     continue

            # SAFETY: ensure we did NOT land on Details page
            if "/cpt-codes/" in driver.current_url and "/range/" not in driver.current_url:
                logger.warning("Landed on Details page unexpectedly for %s", cpt_code)
                record_failed_cpt(cpt_code, "Unexpected navigation to Details page")
                go_to_codify_home(driver)
                continue

            # NOW click Modifier X
            if not click_modifier_x_tab(driver, cpt_code):
                record_failed_cpt(cpt_code, "Modifier X link mismatched or did not open")
                go_to_codify_home(driver)
                continue



            try:
                records = scrape_modifier_x_modal(driver, cpt_code)

                if not records:
                    record_failed_cpt(cpt_code, "Modifier X table empty")

                series_csv = get_series_csv_path(cpt_code, Path(args.output_dir))

                append_rows(
                    records,
                    fieldnames=["cpt_code", "modifier", "description", "allowed_entity"],
                    csv_path=series_csv
                )

                close_modifier_x_modal(driver)
                time.sleep(0.3)
                # go_to_codify_home(driver)

            except Exception as e:
                record_failed_cpt(cpt_code, f"Exception during Modifier X scrape: {str(e)}")
                go_to_codify_home(driver)
                continue

        logger.info("Finished main run. Finalizing outputs.")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()

