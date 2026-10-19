#!/usr/bin/env python3
"""
cpt_modifier_x_scraper.py - Updated with Crosswalks->Modifiers extraction

Search -> Scrape -> Next loop scraper for AAPC Codify CPT pages.
Now includes extraction of Crosswalks -> Modifiers information.
"""
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

# Config
DEFAULT_YEAR = "2025"
DEFAULT_HEADLESS = True
DEFAULT_MAX_RETRIES = 3
DEFAULT_THROTTLE = 0.5
INCREMENTAL_CSV = "./outputs/cpt_scraped_incremental.csv"
CHROMEDRIVER_PATH =(r".\chromedriver\chromedriver-win64\chromedriver.exe")

FAILED_CSV_PATH = Path("./outputs/failed_modifier_x_cpts.csv")
MASTER_CSV_PATH = Path("./outputs/cpt_modifier_x_master.csv")

# Logging
logger = logging.getLogger("aapc_search_loop")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_console)

# ============ UTILITY FUNCTIONS ============
def ensure_dirs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts" / "screenshots").mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts" / "failures").mkdir(parents=True, exist_ok=True)

def now_ts_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def save_screenshot(driver, path: Path) -> None:
    try:
        driver.save_screenshot(str(path))
    except Exception as e:
        logger.warning(f"Screenshot save failed: {e}")

def save_html_dump(driver, path: Path) -> None:
    try:
        path.write_text(driver.page_source, encoding="utf-8")
    except Exception as e:
        logger.warning(f"HTML dump failed: {e}")

def random_throttle(base: float) -> None:
    time.sleep(base + random.random() * base)

def get_series_csv_path(cpt_code: str, output_dir: Path) -> Path:
    first_digit = cpt_code[0] if cpt_code and cpt_code[0].isdigit() else "other"
    return output_dir / f"cpt_modifier_x_{first_digit}_series.csv"

def record_failed_cpt(cpt_code: str, reason: str):
    logger.warning("Recording failed CPT %s | Reason: %s", cpt_code, reason)

    row = {
        "CPT_CODE": cpt_code,
        "REASON": reason,
        "TIMESTAMP": now_ts_iso()
    }

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

def map_color_to_entity(color):
    if not color:
        return "P"

    color = color.lower()
    if "234, 85, 1" in color or "#ea5501" in color:
        return "ASC"
    if "0, 105, 255" in color or "#0069ff" in color:
        return "ASC & P"
    return "P"

# ============ SELENIUM SETUP ============
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

# ============ WAIT HELPERS ============
def wait_for(driver, by, locator, timeout: int = 20, condition: str = "presence"):
    cond_map = {
        "clickable": EC.element_to_be_clickable((by, locator)),
        "visible": EC.visibility_of_element_located((by, locator)),
        "presence": EC.presence_of_element_located((by, locator)),
    }
    return WebDriverWait(driver, timeout).until(cond_map[condition])

def wait_for_optional(driver, by, locator, timeout: int = 5, condition: str = "presence"):
    try:
        return wait_for(driver, by, locator, timeout=timeout, condition=condition)
    except (TimeoutException, Exception):
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
            ("xpath", "//button[contains(@class,'close')]"),
            ("xpath", "//button[contains(text(),'Skip')]"),
            ("xpath", "//button[contains(text(),'×')]"),
        ]
        for by, sel in candidates:
            els = safe_find_elements(driver, By.XPATH if by == "xpath" else By.CSS_SELECTOR, sel)
            for el in els:
                if el.is_displayed():
                    try:
                        el.click()
                        time.sleep(0.4)
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", el)
                        except Exception:
                            pass
    except Exception:
        pass

# ============ LOGIN ============
def login_to_aapc(driver, username: str, password: str, allow_placeholder: bool, max_retries: int = 3) -> bool:
    if (username.startswith("PLACEHOLDER") or password.startswith("PLACEHOLDER")) and not allow_placeholder:
        logger.warning("Credentials are placeholder values")
        return False

    try:
        driver.get("https://www.aapc.com/")
        
        sign_in = wait_for(driver, By.ID, "btnSignIn", timeout=10, condition="clickable")
        sign_in.click()

        user_el = wait_for(driver, By.ID, "userProvidedSignInName", timeout=20, condition="visible")
        user_el.clear()
        user_el.send_keys(username)
        next_btn = wait_for(driver, By.ID, "next", timeout=10, condition="clickable")
        next_btn.click()

        pw_el = wait_for(driver, By.ID, "password", timeout=15, condition="visible")
        pw_el.clear()
        pw_el.send_keys(password)
        cont = wait_for(driver, By.ID, "continue", timeout=10, condition="clickable")
        cont.click()

        wait_for(driver, By.XPATH, "//a[contains(@href,'/codes/all_coding_tools/home')]", timeout=25)
        logger.info("Login successful")
        return True

    except Exception as e:
        logger.exception(f"Login error: {e}")
        return False

# def ensure_year_selected(driver, year: str) -> None:
#     try:
#         sel_elem = wait_for_optional(driver, By.CSS_SELECTOR, "select", timeout=8)
#         if sel_elem:
#             select = Select(sel_elem)
#             for opt in select.options:
#                 if str(year) in opt.text:
#                     select.select_by_value(opt.get_attribute("value"))
#                     logger.info(f"Selected year: {opt.text}")
#                     time.sleep(0.5)
#                     break
#     except Exception as e:
#         logger.warning(f"Year selection error: {e}")

def ensure_year_selected(driver, year: str) -> None:
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "select"))
        )

        selects = driver.find_elements(By.TAG_NAME, "select")
        for sel_elem in selects:
            try:
                select = Select(sel_elem)
                for opt in select.options:
                    if str(year) in opt.text:
                        select.select_by_value(opt.get_attribute("value"))
                        logger.info(f"Selected year: {opt.text}")
                        time.sleep(0.5)
                        return
            except StaleElementReferenceException:
                continue
    except Exception as e:
        logger.warning(f"Year selection skipped: {e}")


# ============ CHECKPOINT/CSV ============
def append_rows(rows: List[Dict], fieldnames: Optional[List[str]] = None):
    if not rows:
        return
    os.makedirs(os.path.dirname(INCREMENTAL_CSV), exist_ok=True)
    exists = os.path.exists(INCREMENTAL_CSV)
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(INCREMENTAL_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: (r.get(k) or "") for k in fieldnames})

def immediate_checkpoint_append(checkpoint_path: Path, record: dict):
    try:
        if checkpoint_path.exists():
            cp = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        else:
            cp = {"records": [], "last_scraped": None}
        cp.setdefault("records", []).append(record)
        cp["last_scraped"] = record.get("code")
        tmp = Path(str(checkpoint_path) + ".tmp")
        tmp.write_text(json.dumps(cp, indent=2), encoding="utf-8")
        tmp.replace(checkpoint_path)
    except Exception as e:
        logger.exception(f"Checkpoint failed: {e}")

# ============ SCRAPING HELPERS ============
def _expand_read_more_in(el):
    try:
        buttons = el.find_elements(By.XPATH, ".//a[contains(.,'Read More') or contains(.,'Read more')]")
        for b in buttons:
            try:
                b.click()
                time.sleep(0.25)
            except Exception:
                pass
    except Exception:
        pass

# def scrape_crosswalks_modifiers(driver) -> str:
#     """Extract Crosswalks -> Modifiers information"""
#     try:
#         logger.info("Extracting Crosswalks -> Modifiers")
        
#         # Step 1: Click Crosswalks panel
#         crosswalks_panel = wait_for_optional(
#             driver, By.XPATH, 
#             "//div[@id='crosswalks']//h4[@href='#collapse_crosswalk']",
#             timeout=5, condition="clickable"
#         )
        
#         if crosswalks_panel:
#             driver.execute_script("arguments[0].scrollIntoView({block:'center'});", crosswalks_panel)
#             time.sleep(0.3)
#             driver.execute_script("arguments[0].click();", crosswalks_panel)
#             logger.info("Clicked Crosswalks panel")
#             time.sleep(0.8)
#         else:
#             logger.warning("Crosswalks panel not found")
#             return ""
        
#         # Step 2: Click Modifiers tab
#         modifiers_tab = wait_for_optional(
#             driver, By.XPATH,
#             "//li[@id='cpt_cross_modifier']/a[@href='#cpt-modifier-asc']",
#             timeout=5, condition="clickable"
#         )
        
#         if modifiers_tab:
#             driver.execute_script("arguments[0].scrollIntoView({block:'center'});", modifiers_tab)
#             time.sleep(0.3)
#             driver.execute_script("arguments[0].click();", modifiers_tab)
#             logger.info("Clicked Modifiers tab")
#             time.sleep(1.5)  # Wait for AJAX
#         else:
#             logger.warning("Modifiers tab not found")
#             return ""
        
#         # Step 3: Extract content
        
#         content_selectors = [
#             "//div[@id='cpt-modifier-asc']",
#             "//div[@id='cpt_mod_ascp_cross']",
#             "//div[contains(@class,'tab-pane') and contains(@id,'modifier')]",
#         ]
        
        
#         for selector in content_selectors:
#             content_el = wait_for_optional(driver, By.XPATH, selector, timeout=3)
#             if content_el and content_el.text.strip():
#                 _expand_read_more_in(content_el)
#                 text = content_el.text.strip()
                
#                 # Extract tables if present
#                 try:
#                     tables = content_el.find_elements(By.TAG_NAME, "table")
#                     if tables:
#                         table_data = []
#                         for table in tables:
#                             rows = table.find_elements(By.TAG_NAME, "tr")
#                             for row in rows:
#                                 cells = row.find_elements(By.TAG_NAME, "td") or row.find_elements(By.TAG_NAME, "th")
#                                 row_text = " | ".join([c.text.strip() for c in cells if c.text.strip()])
#                                 if row_text:
#                                     table_data.append(row_text)
#                         if table_data:
#                             text += "\n\nTable:\n" + "\n".join(table_data)
#                 except Exception:
#                     pass
                
#                 logger.info(f"Extracted Modifiers data ({len(text)} chars)")
#                 return " ".join(text.split())
        
#         logger.warning("Modifiers content not found")
#         return ""
        
#     except Exception as e:
#         logger.exception(f"Modifiers extraction error: {e}")
#         return ""


def scrape_modifier_x_modal(driver, cpt_code):
    rows_data = []

    try:
        title_el = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, "messageModal_title"))
        )
        modal_title = title_el.text.strip()

        if cpt_code not in modal_title:
            record_failed_cpt(cpt_code, "Modifier modal title mismatch")
            return []

        rows = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//tbody[contains(@class,'points_table_scrollbar')]/tr")
            )
        )

        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 2:
                continue

            modifier = tds[0].text.strip()
            description = tds[1].text.strip()
            color = tds[1].value_of_css_property("color")

            rows_data.append({
                "cpt_code": cpt_code,
                "modifier": modifier,
                "description": description,
                "allowed_entity": map_color_to_entity(color)
            })

    except Exception as e:
        record_failed_cpt(cpt_code, str(e))

    return rows_data

# from selenium.webdriver.common.action_chains import ActionChains

# def navigate_to_codify(driver) -> bool:
#     """
#     Navigate to Codify via hover menu (AAPC mega-menu).
#     This EXACTLY matches real user + Katalon behavior.
#     """
#     try:
#         logger.info("Navigating to Codify via menu")

#         # 🔒 Ensure dashboard is fully loaded
#         WebDriverWait(driver, 20).until(
#             EC.presence_of_element_located((By.TAG_NAME, "body"))
#         )

#         time.sleep(2)  # allow header JS to finish

#         # 🔹 Locate Codify menu item (LI, not A)
#         codify_li = WebDriverWait(driver, 20).until(
#             EC.presence_of_element_located(
#                 (By.ID, "ctl00_preTopMegaMenu_coderPretop")
#             )
#         )

#         codify_a = codify_li.find_element(By.TAG_NAME, "a")

#         # 🔹 Hover → Click (MANDATORY)
#         actions = ActionChains(driver)
#         actions.move_to_element(codify_a).pause(1).click().perform()

#         logger.info("Clicked Codify menu")

#         # 🔒 Codify page is READY only when search box appears
#         WebDriverWait(driver, 30).until(
#             EC.visibility_of_element_located((By.ID, "top_search"))
#         )

#         logger.info("Entered Codify successfully")
#         logger.info(f"Codify URL: {driver.current_url}")
#         return True

#     except Exception as e:
#         logger.exception("Failed to navigate to Codify")
#         return False


def scrape_code_page(driver, code_url: str, code_range: str) -> List[Dict]:
    """Main scraping function - now includes Crosswalks Modifiers"""
    data = {
        "code": None,
        "short_description": None,
        "lay_term": None,
        "guidelines": None,
        "crosswalks_modifiers": None,  # NEW FIELD
        "code_range": code_range,
        "source_url": code_url,
        "scrape_timestamp": now_ts_iso(),
    }

    try:
        try:
            driver.get(code_url)
        except Exception:
            pass

        random_throttle(DEFAULT_THROTTLE)
        dismiss_popups(driver)
        # Extract CPT code (authoritative)
        header_el = wait_for_optional(
            driver,
            By.XPATH,
            "//h1[contains(@class,'pull-left')]",
            timeout=6
        )

        if header_el:
            m = re.search(r"\b\d{3,5}[A-Z]{0,2}\b", header_el.text)
            cpt_code = m.group(0) if m else code_url.rstrip("/").split("/")[-1]
        else:
            cpt_code = code_url.rstrip("/").split("/")[-1]

        data["code"] = cpt_code

        # Ensure we are on CPT details page (not range page)
        if not ensure_cpt_details_page(driver, cpt_code):
            record_failed_cpt(cpt_code, "Failed to open CPT details page")
            return []



        # Extract SHORT DESCRIPTION
        # try:
        #     desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info']//div[contains(@class,'panel-body')]", timeout=6)
        #     if desc_el:
        #         _expand_read_more_in(desc_el)
        #         data["short_description"] = " ".join(desc_el.text.split())
        #     else:
        #         data["short_description"] = ""
        # except Exception:
        #     data["short_description"] = ""

        # Extract GUIDELINES
        # try:
        #     tab = wait_for_optional(driver, By.LINK_TEXT, "Guidelines", timeout=4, condition="clickable")
        #     if tab:
        #         driver.execute_script("arguments[0].click();", tab)
        #         time.sleep(0.6)
            
        #     panel = wait_for_optional(driver, By.XPATH, "//*[@id='cpt_guidelines']", timeout=5)
        #     if panel:
        #         _expand_read_more_in(panel)
        #         txt = " ".join(panel.text.split()).strip()
        #         if len(txt) > 40:
        #             data["guidelines"] = txt
        #         else:
        #             data["guidelines"] = ""
        #     else:
        #         data["guidelines"] = ""
        # except Exception:
        #     data["guidelines"] = ""

        # Extract LAY TERM
        # try:
        #     lay_tab = wait_for_optional(driver, By.LINK_TEXT, "Lay Term", timeout=3, condition="clickable")
        #     if lay_tab:
        #         driver.execute_script("arguments[0].click();", lay_tab)
        #         time.sleep(0.3)
            
        #     lay_el = wait_for_optional(driver, By.XPATH, "//*[@id='cpt_layterm']//div[contains(@class,'panel-body')]", timeout=3)
        #     if lay_el:
        #         _expand_read_more_in(lay_el)
        #         data["lay_term"] = " ".join(lay_el.text.split()).strip()
        #     else:
        #         data["lay_term"] = ""
        # except Exception:
        #     data["lay_term"] = ""

        # Extract CROSSWALKS -> MODIFIERS (NEW)
        # ================== MODIFIER X ONLY ==================

        # Extract CPT code (keep this logic)
        cpt_code = data.get("code")
        if not cpt_code:
            record_failed_cpt("UNKNOWN", "CPT code not found")
            return []

        # Open Crosswalks panel
        crosswalks_panel = wait_for_optional(
            driver, By.XPATH,
            "//div[@id='crosswalks']//h4",
            timeout=5, condition="clickable"
        )
        if not crosswalks_panel:
            record_failed_cpt(cpt_code, "Crosswalks panel not found")
            return []

        driver.execute_script("arguments[0].click();", crosswalks_panel)
        time.sleep(0.6)


        # Click Modifiers tab (MANDATORY — triggers AJAX)
        mod_tab = wait_for(
            driver,
            By.XPATH,
            "//li[@id='cpt_cross_modifier']/a",
            timeout=10,
            condition="clickable"
        )
        driver.execute_script("arguments[0].click();", mod_tab)

        # Wait for AJAX-loaded modifier rows
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//div[@id='cpt_mod_ascp_cross']//tbody[@class='points_table_scrollbar']/tr"
                )
            )
        )



        # driver.execute_script("arguments[0].click();", mod_tab)
        # time.sleep(0.5)

        # wait_for_optional(
        #     driver,
        #     By.ID,
        #     "cpt-modifier-asc",
        #     timeout=10,
        #     condition="presence",
        # )


        # 🔹 Scrape modifiers directly from crosswalk tab
        modifier_rows = scrape_modifiers_crosswalk(driver, cpt_code)

        # if not modifier_rows:
        #     record_failed_cpt(cpt_code, "No modifiers found after opening Modifiers tab")

        return modifier_rows

    except Exception as e:
        record_failed_cpt(data.get("code", "UNKNOWN"), f"scrape_code_page error: {e}")
        return []

# def ensure_cpt_details_page(driver, cpt_code: str) -> bool:
#     if "cpt-codes-range" not in driver.current_url:
#         return True

#     logger.info("Range page detected, clicking CPT-scoped Details")

#     details_xpath = (
#         f"//div[@id='{cpt_code}']//div[@class='code-link']"
#         "//a[normalize-space()='Details']"
#     )

#     details_link = WebDriverWait(driver, 15).until(
#         EC.element_to_be_clickable((By.XPATH, details_xpath))
#     )

#     driver.execute_script("arguments[0].click();", details_link)

#     WebDriverWait(driver, 15).until(
#         lambda d: "cpt-codes-range" not in d.current_url
#     )

#     WebDriverWait(driver, 10).until(
#         EC.visibility_of_element_located(
#             (By.XPATH, "//h1[contains(@class,'pull-left')]")
#         )
#     )

#     return True

def ensure_cpt_details_page(driver, cpt_code: str) -> bool:
    if "cpt-codes-range" not in driver.current_url:
        return True

    logger.info("Range page detected, clicking Details")

    # Prefer CPT-scoped Details, fallback to first visible Details
    xpaths = [
        f"//div[contains(@id,'{cpt_code}')]//a[normalize-space()='Details']",
        "//a[normalize-space()='Details']"
    ]

    details_link = None
    for xp in xpaths:
        try:
            details_link = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, xp))
            )
            if details_link:
                break
        except TimeoutException:
            continue

    if not details_link:
        logger.warning("Details link not found on range page")
        return False

    driver.execute_script("arguments[0].click();", details_link)

    WebDriverWait(driver, 15).until(
        lambda d: "cpt-codes-range" not in d.current_url
    )

    WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located(
            (By.XPATH, "//h1[contains(@class,'pull-left')]")
        )
    )

    return True

def append_modifier_rows(csv_path: Path, rows: list):
    if not rows:
        return

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["cpt_code", "modifier", "description", "allowed_entity"]
        )
        if not file_exists:
            writer.writeheader()

        for row in rows:
            writer.writerow(row)

# ============ NAVIGATION ============
def search_and_open_code(driver, code_token: str) -> Optional[str]:
    """Search for a code and navigate to its page"""
    logger.info(f"Searching: {code_token}")
    try:
        dismiss_popups(driver)
        
        search = wait_for(driver, By.ID, "top_search", timeout=10, condition="clickable")
        driver.execute_script("arguments[0].click();", search)
        search.clear()
        search.send_keys(code_token)
        search.send_keys(Keys.ENTER)
        
        random_throttle(DEFAULT_THROTTLE)
        dismiss_popups(driver)
        time.sleep(1.0)

        wait_for_optional(driver, By.XPATH, "//h1[contains(@class,'pull-left')]", timeout=8)
        return driver.current_url
        
    except Exception as e:
        logger.exception(f"Search failed: {e}")
        return None

def click_next_code(driver) -> bool:
    """Click the 'Next' button to navigate to next code"""
    try:
        cur = driver.current_url
        
        nxt_icon = wait_for_optional(driver, By.XPATH, "//div[@id='breadcrumbs']//a[2]/div/i", timeout=3)
        if nxt_icon:
            parent_a = nxt_icon.find_element(By.XPATH, "ancestor::a[1]")
            driver.execute_script("arguments[0].click();", parent_a)
            random_throttle(DEFAULT_THROTTLE)
            dismiss_popups(driver)
            time.sleep(0.5)
            return driver.current_url != cur
        
        logger.info("No next control found")
        return False
        
    except Exception as e:
        logger.exception(f"Next click error: {e}")
        return False

# ============ MAIN LOOP ============
def run_search_to_stop_loop(
    driver,
    start_code: str,
    stop_code: str,
    checkpoint_path: Path,   # kept for signature compatibility (unused)
    output_dir: Path,
    throttle: float = DEFAULT_THROTTLE,
    max_cycles: int = 2000,
):
    """
    Main scraping loop
    Scrapes ONLY Crosswalks -> Modifier-X data
    One CPT -> N modifier rows
    """

    logger.info("Starting modifier-X loop: %s -> %s", start_code, stop_code)

    # 🔹 Open first CPT
    first_url = search_and_open_code(driver, start_code)
    if not first_url:
        logger.error("Could not open start code: %s", start_code)
        record_failed_cpt(start_code, "Search failed for start code")
        return

    cycles = 0
    prev_marker = None

    while cycles < max_cycles:
        cycles += 1

        try:
            cur_url = driver.current_url

            # 🔹 Scrape modifier rows (list)
            modifier_rows = scrape_code_page(
                driver,
                cur_url,
                code_range=start_code
            )

            if modifier_rows:
                # 🔹 Write MASTER CSV
                append_modifier_rows(MASTER_CSV_PATH, modifier_rows)

                # 🔹 Write SERIES CSV
                cpt_code = modifier_rows[0]["cpt_code"]
                series_csv = get_series_csv_path(cpt_code, output_dir)
                append_modifier_rows(series_csv, modifier_rows)

                logger.info(
                    "Saved %d modifiers for CPT %s (cycle %d)",
                    len(modifier_rows),
                    cpt_code,
                    cycles,
                )
            else:
                # scrape_code_page already records failure reason
                logger.warning(
                    "No modifier rows returned for URL: %s (cycle %d)",
                    cur_url,
                    cycles,
                )

            # 🔹 Stop condition
            if stop_code and modifier_rows:
                if modifier_rows[0]["cpt_code"] == stop_code:
                    logger.info("Reached stop code: %s", stop_code)
                    break

            # 🔹 Navigate to next CPT
            if not click_next_code(driver):
                logger.info("No next CPT available - stopping loop")
                break

            random_throttle(throttle)

            # 🔹 Same-page loop detection
            new_header = wait_for_optional(
                driver,
                By.XPATH,
                "//h1[contains(@class,'pull-left')]",
                timeout=6,
            )
            new_marker = (
                new_header.text.strip()
                if new_header
                else driver.current_url
            )

            if new_marker == prev_marker:
                logger.warning("Same page detected again - stopping loop")
                break

            prev_marker = new_marker

        except Exception as e:
            logger.exception("Fatal loop error at cycle %d: %s", cycles, e)
            record_failed_cpt(
                "UNKNOWN",
                f"Loop exception at cycle {cycles}: {str(e)}"
            )
            break

    logger.info("Modifier-X scraping completed after %d cycles", cycles)

def scrape_modifiers_crosswalk(driver, cpt_code: str) -> list:
    """
    Scrape ALL modifiers directly from Crosswalks -> Modifiers tab.
    One CPT -> N modifier rows.
    """
    rows_data = []

    try:
        # Ensure modifiers container is present
        container = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "cpt_mod_ascp_cross"))
        )


        # rows = container.find_elements(By.XPATH, ".//tbody/tr")
        rows = container.find_elements(
            By.XPATH,
            ".//tbody[@class='points_table_scrollbar']/tr"
        )


        if not rows:
            record_failed_cpt(cpt_code, "No modifier rows found in crosswalk")
            return []

        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) < 2:
                continue

            modifier = tds[0].text.strip()
            description = tds[1].text.strip()

            color = tds[1].value_of_css_property("color")
            allowed_entity = map_color_to_entity(color)

            rows_data.append({
                "cpt_code": cpt_code,
                "modifier": modifier,
                "description": description,
                "allowed_entity": allowed_entity,
            })

    except Exception as e:
        record_failed_cpt(cpt_code, f"Modifiers crosswalk scrape error: {e}")

    return rows_data

# ============ MAIN ============
def main():
    parser = argparse.ArgumentParser(description="AAPC CPT Scraper with Crosswalks->Modifiers")
    parser.add_argument("--headless", action="store_true", default=DEFAULT_HEADLESS)
    parser.add_argument("--no-headless", action="store_false", dest="headless")
    parser.add_argument("--year", default=DEFAULT_YEAR)
    parser.add_argument("--output-dir", default="./outputs")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE)
    parser.add_argument("--allow-placeholder", action="store_true")
    parser.add_argument("--start-code", required=True, help="Start CPT code")
    parser.add_argument("--stop-code", default="", help="Stop CPT code (inclusive)")
    args = parser.parse_args()

    logger.setLevel(getattr(logging, args.log_level.upper()))
    output_dir = Path(args.output_dir)
    ensure_dirs(output_dir)

    username = "YOUR_AAPC_USERNAME"
    password = "YOUR_AAPC_PASSWORD"
    checkpoint_path = output_dir / "checkpoint.json"

    driver = build_driver(args.headless)
    logger.info(f"Driver created (headless={args.headless})")

    try:
        if not login_to_aapc(driver, username, password, args.allow_placeholder):
            logger.error("Login failed")
            sys.exit(2)

        logger.info("Opening Codify directly")
        driver.get("https://www.aapc.com/codes/all_coding_tools/home")

        wait_for(driver, By.ID, "top_search", timeout=20, condition="visible")
        logger.info("Codify ready")



        # Skip modal if present
        skip_btn = wait_for_optional(driver, By.XPATH, "//form[@id='migrate_user_terms']//button[2]", timeout=5)
        if skip_btn:
            driver.execute_script("arguments[0].click();", skip_btn)
            logger.info("Skipped modal")

        ensure_year_selected(driver, args.year)

        # Run main loop
        run_search_to_stop_loop(driver, args.start_code.strip(), args.stop_code.strip(), 
                               checkpoint_path, output_dir, throttle=args.throttle)

        logger.info("Scraping complete!")
        
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()