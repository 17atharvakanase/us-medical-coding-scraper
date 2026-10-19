
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
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import pandas as pd
import csv

# ---------------- Config defaults ----------------
DEFAULT_YEAR = "2025"
DEFAULT_HEADLESS = True
DEFAULT_MAX_RETRIES = 3
DEFAULT_THROTTLE = 0.5
INCREMENTAL_CSV = "./outputs/cpt_scraped_incremental.csv"

# ---------------- Selectors / constants ----------------
SEARCH_INPUT_ID = "top_search"
DETAILS_LINK_TEXT = "Details"
GUIDELINES_LINK_TEXT = "Guidelines"
LAYTERM_LINK_TEXT = "Lay Term"
FULL_LAYTERM_ID = "fullLayterm"
BREADCRUMB_NEXT_ICON_XPATH = "//div[@id='breadcrumbs']/div/div[2]/div/a[2]/div/i"

# ---------------- HCPCS XPaths (robust + fallbacks) ----------------
HCPCS_SELECTORS = {
    # link to click in search results (keeps existing absolute xpath if it works)
    "code_link_xpath": "/html/body/div[6]/section/div/div/div[2]/div/div/div[3]/div[4]/div[1]/div[1]/div[1]/div[1]/a",

    # details button is not present for modifiers — keep as fallback but not required
    "details_click_xpath": "/html/body/div[6]/section/div/div/div[2]/div/div/div[3]/div[4]/div[1]/div[1]/div[4]/a[1]",

    # Short description (Modifier Descriptor)
    "short_desc_xpath": "//*[@id='code_desc']//div[contains(@class,'tab-pane')]",

    # Lay term — prefer collapse5 but allow fallback header-match (works if ids vary)
    "layterm_content_xpath": "//*[@id='collapse5']//div[contains(@class,'tab-pane')]",
    "layterm_fallback_xpath": "//h4[contains(translate(normalize-space(.),'LAY TERM','lay term'),'lay term')]/following::div[1]//div[contains(@class,'tab-pane')]",
    "layterm_header_xpath": "//h4[contains(translate(normalize-space(.),'LAY TERM','lay term'),'lay term')]",

    # read-more candidate inside layterm
    "layterm_readmore_xpath": "//*[contains(@id,'readMore') or contains(translate(.,'READ MORE','read more'),'read more') or contains(.,'Read More') or contains(.,'Read more')]",

    # Guidelines — prefer collapse8 then fallback to header-match
    "guidelines_content_xpath": "//*[@id='collapse8']//div[contains(@class,'tab-pane')]",
    "guidelines_fallback_xpath": "//h4[contains(translate(normalize-space(.),'MODIFIER GUIDELINES','modifier guidelines'),'modifier guidelines')]/following::div[1]//div[contains(@class,'tab-pane')]",
    "guidelines_header_xpath": "//h4[contains(translate(normalize-space(.),'MODIFIER GUIDELINES','modifier guidelines'),'modifier guidelines')]",
}

# ---------------- Logging ----------------
logger = logging.getLogger("aapc_search_loop")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_console)

# ---------------- Utils ----------------
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

# ---------------- Selenium builder (local chromedriver) ---------------
def build_driver(headless: bool) -> webdriver.Chrome:
    from selenium.webdriver.chrome.service import Service

    # 👉 CHANGE THIS TO YOUR ACTUAL chromedriver.exe PATH
    CHROMEDRIVER_PATH = r".\chromedriver\chromedriver-win64\chromedriver.exe"

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

    #  Use your local driver instead of webdriver-manager
    service = Service(CHROMEDRIVER_PATH)

    # Silence selenium noise
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

# ---------------- Wait helpers ----------------
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

# ---------------- I/O helpers ----------------
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

# ---------------- Expand/read helpers from prior script ----------------
def _expand_read_more_in(el):
    """
    Robust read-more expander that:
      - executes inline onclick JS if present (preferred)
      - falls back to JS click (scrollIntoView + click)
      - falls back to normal click
    Works on the CPT pages and HCPCS layterm containers.
    """
    try:
        # get driver instance from element (same pattern used in old code)
        driver = getattr(el, "_parent", None)
        # find candidate anchors inside the element
        buttons = el.find_elements(By.XPATH, ".//a[contains(.,'Read More') or contains(.,'Read more') or contains(.,'... Read More') or contains(.,'Read More ›') or contains(.,'... Read more')]")
        for b in buttons:
            try:
                onclick = b.get_attribute("onclick")
                if onclick and onclick.strip():
                    # execute the exact inline JS if provided (this triggers functions like readMoreLaytermFunc(2))
                    try:
                        if driver:
                            driver.execute_script(onclick)
                        else:
                            # fallback: try clicking if driver not found
                            try:
                                b.click()
                            except Exception:
                                pass
                        time.sleep(0.30)
                        continue
                    except Exception:
                        # fall through to JS click attempts
                        pass
                # Try JS click (scroll + click)
                try:
                    if driver:
                        driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", b)
                    else:
                        b.click()
                    time.sleep(0.30)
                    continue
                except Exception:
                    # as last resort try normal click
                    try:
                        b.click()
                        time.sleep(0.30)
                        continue
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        # silently ignore expand failures - caller will fallback to other heuristics
        pass

# --- Helper: robustly activate a read-more element (executes onclick JS if present) ---
def activate_read_more_element(driver, el):
    """
    Try to trigger a 'Read More' anchor reliably.
    Steps:
      1) If the element has an inline onclick attribute, execute it directly (preferred).
      2) Otherwise try JS click (scrollIntoView + click).
      3) Then fallback to normal click.
    Returns True if we believe the expansion was triggered.
    """
    try:
        if el is None:
            return False
        onclick = el.get_attribute("onclick")
        if onclick and onclick.strip():
            try:
                # execute the inline JS exactly as written (e.g. "readMoreLaytermFunc(2);")
                driver.execute_script(onclick)
                return True
            except Exception:
                # fall through to JS click fallback
                pass
        try:
            driver.execute_script("arguments[0].scrollIntoView(true); arguments[0].click();", el)
            return True
        except Exception:
            try:
                el.click()
                return True
            except Exception:
                return False
    except Exception:
        return False

# ---------------- Scrape a code page (CPT or HCPCS-aware) ----------------
def scrape_code_page(driver, code_url: str, code_range: str, code_type: str = "CPT") -> Dict[str, Optional[str]]:
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
        # Ensure on the intended URL (but sometimes we are already there)
        try:
            driver.get(code_url)
        except Exception:
            # not fatal; continue with whatever is loaded
            pass

        random_throttle(DEFAULT_THROTTLE)
        dismiss_popups(driver)

        # --- CODE extraction: broader regex to accept J0120 etc. ---
        try:
            code_from_url = code_url.rstrip("/").split("/")[-1]
            header_el = wait_for_optional(driver, By.XPATH, "//h1[contains(@class,'pull-left') or contains(@class,'page-title')]", timeout=6, condition="visible")
            header = header_el.text.strip() if header_el else ""
            code_regex = re.compile(r"\b[A-Z]?\d{3,5}[A-Z]{0,2}\b", re.IGNORECASE)
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

        # --- SHORT DESCRIPTION (HCPCS-aware) ---
        try:
            if code_type.upper() == "HCPCS":
                sd_el = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS["short_desc_xpath"], timeout=4, condition="visible")
                if sd_el:
                    data["short_description"] = " ".join(sd_el.text.split())
                else:
                    # fallback generic
                    desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info']//div[contains(@class,'panel-body') or contains(@class,'description')]", timeout=4, condition="visible")
                    data["short_description"] = " ".join(desc_el.text.split()) if desc_el else ""
            else:
                desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info']//div[contains(@class,'panel-body') or contains(@class,'description')]", timeout=6, condition="visible")
                if not desc_el:
                    desc_el = wait_for_optional(driver, By.XPATH, "//div[@id='description_info' or contains(@class,'cpt-description')]", timeout=4, condition="presence")
                if desc_el:
                    _expand_read_more_in(desc_el)
                    data["short_description"] = " ".join(desc_el.text.split())
                else:
                    p_el = wait_for_optional(driver, By.XPATH, "//div[contains(@class,'cpt-description')]//p", timeout=3, condition="presence")
                    data["short_description"] = p_el.text.strip() if p_el else ""
        except Exception as e:
            logger.debug("short_description extraction exception: %s", e)
            data["short_description"] = ""

        # ---------------- HCPCS extraction (robust lay term & guidelines) ----------------
        try:
            # initialize
            data["lay_term"] = ""
            data["guidelines"] = ""

            if code_type.upper() == "HCPCS":
                # helper to try multiple XPaths and return first meaningful text + xpath used
                def try_xpaths_and_get_text(xpath_list, label):
                    for xp in xpath_list:
                        if not xp:
                            continue
                        try:
                            el = wait_for_optional(driver, By.XPATH, xp, timeout=4, condition="visible")
                            if el:
                                logger.debug("Found %s using xpath: %s", label, xp)
                                # expand internal read-more anchors if present
                                try:
                                    _expand_read_more_in(el)
                                except Exception:
                                    pass
                                # read via JS for robustness
                                try:
                                    txt = driver.execute_script("return arguments[0].textContent || arguments[0].innerText || '';", el) or ""
                                except Exception:
                                    txt = el.text or ""
                                txt = " ".join(txt.split()).strip()
                                # sanity checks: not too short, not just label text
                                if txt and len(txt) > 20 and label.lower() not in txt.lower():
                                    return txt, xp
                        except Exception:
                            continue
                    return "", None

                # 1) SHORT DESCRIPTION (already attempted above but ensure normalized)
                try:
                    if not data.get("short_description"):
                        sd_el = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS.get("short_desc_xpath"), timeout=4, condition="visible")
                        if sd_el:
                            data["short_description"] = " ".join(sd_el.text.split())
                except Exception:
                    pass

                # 2) LAY TERM extraction
                try:
                    dismiss_popups(driver)

                    # Try to click header first (executes inline onclick if present)
                    try:
                        head = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS.get("layterm_header_xpath"), timeout=2, condition="clickable")
                        if head:
                            try:
                                driver.execute_script("arguments[0].scrollIntoView(true);", head)
                                onclick = head.get_attribute("onclick")
                                if onclick and onclick.strip():
                                    try:
                                        driver.execute_script(onclick)
                                    except Exception:
                                        try:
                                            driver.execute_script("arguments[0].click();", head)
                                        except Exception:
                                            try:
                                                head.click()
                                            except Exception:
                                                pass
                                else:
                                    try:
                                        driver.execute_script("arguments[0].click();", head)
                                    except Exception:
                                        try:
                                            head.click()
                                        except Exception:
                                            pass
                                time.sleep(0.35)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    lay_xpath_candidates = [
                        HCPCS_SELECTORS.get("layterm_content_xpath"),
                        HCPCS_SELECTORS.get("layterm_fallback_xpath"),
                        "//*[@id='lay_desc']//div[contains(@class,'tab-pane')]",
                        "//h4[contains(.,'Lay Term')]/following::div[1]//div[contains(@class,'tab-pane')]",
                    ]
                    lay_txt, used = try_xpaths_and_get_text(lay_xpath_candidates, "lay_term")
                    if lay_txt:
                        data["lay_term"] = lay_txt
                        logger.info("HCPCS lay_term captured (xpath used: %s)", used)
                    else:
                        logger.warning("HCPCS lay_term NOT found using candidate xpaths")
                        # debug artifacts
                        try:
                            ts = int(time.time())
                            ss = Path("./outputs/artifacts/screenshots") / f"layterm_missing_{data.get('code')}_{ts}.png"
                            dump = Path("./outputs/artifacts/failures") / f"layterm_missing_{data.get('code')}_{ts}.html"
                            save_screenshot(driver, ss)
                            save_html_dump(driver, dump)
                            logger.warning("Saved debug artifacts for missing layterm: %s %s", ss, dump)
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("layterm extraction exception: %s", e)
                    data["lay_term"] = data.get("lay_term", "")

                # 3) GUIDELINES extraction
                try:
                    # try header click for guidelines if present
                    try:
                        ghead = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS.get("guidelines_header_xpath"), timeout=2, condition="clickable")
                        if ghead:
                            try:
                                driver.execute_script("arguments[0].scrollIntoView(true);", ghead)
                                onclick = ghead.get_attribute("onclick")
                                if onclick and onclick.strip():
                                    try:
                                        driver.execute_script(onclick)
                                    except Exception:
                                        try:
                                            driver.execute_script("arguments[0].click();", ghead)
                                        except Exception:
                                            try:
                                                ghead.click()
                                            except Exception:
                                                pass
                                else:
                                    try:
                                        driver.execute_script("arguments[0].click();", ghead)
                                    except Exception:
                                        try:
                                            ghead.click()
                                        except Exception:
                                            pass
                                time.sleep(0.35)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    guides_xpath_candidates = [
                        HCPCS_SELECTORS.get("guidelines_content_xpath"),
                        HCPCS_SELECTORS.get("guidelines_fallback_xpath"),
                        "//h4[contains(.,'Modifier Guidelines')]/following::div[1]//div[contains(@class,'tab-pane')]",
                    ]
                    guides_txt, usedg = try_xpaths_and_get_text(guides_xpath_candidates, "guidelines")
                    if guides_txt:
                        data["guidelines"] = guides_txt
                        logger.info("HCPCS guidelines captured (xpath used: %s)", usedg)
                    else:
                        logger.warning("HCPCS guidelines NOT found using candidate xpaths")
                except Exception as e:
                    logger.debug("guidelines extraction exception: %s", e)
                    data["guidelines"] = data.get("guidelines", "")

            else:
                # CPT flow (reuse existing robust logic)
                try:
                    # click lay term tab if present
                    try:
                        lay_tab = wait_for_optional(driver, By.CSS_SELECTOR, "#cpt_layterm, #cpt_layterm-tab, li#cpt_layterm_li", timeout=2, condition="clickable")
                        if lay_tab:
                            try:
                                a = lay_tab.find_element(By.TAG_NAME, "a")
                                driver.execute_script("arguments[0].click();", a)
                                time.sleep(0.2)
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", lay_tab)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    lay_selectors = [
                        "//*[@id='cpt_layterm']//div[contains(@class,'panel-body') or normalize-space(text())]",
                        "//*[@id='cpt_layterm' or contains(@class,'cpt-layterm') or contains(@class,'layterm')]//div[contains(@class,'panel-body') or p or div]",
                        "//a[contains(normalize-space(.),'Lay Term')]/ancestor::li/following-sibling::*[1]//div[contains(@class,'panel-body') or normalize-space(text())]",
                        "//h2[contains(.,'Lay Term') or contains(.,'Lay term')]/following-sibling::*[1]//div[contains(@class,'panel-body') or p]",
                    ]
                    lay_txt = ""
                    for sel in lay_selectors:
                        try:
                            lay_el = wait_for_optional(driver, By.XPATH, sel, timeout=3, condition="visible")
                            if lay_el:
                                _expand_read_more_in(lay_el)
                                txt = " ".join(lay_el.text.split()).strip()
                                if txt and txt.lower() != "upcoming & historical information":
                                    lay_txt = txt
                                    break
                        except Exception:
                            continue
                    if not lay_txt:
                        # fallback paragraph search
                        try:
                            candidates = driver.find_elements(By.XPATH, "//*[contains(translate(., 'LAY TERM', 'lay term'), 'lay term') or contains(translate(., 'Lay Term', 'lay term'), 'lay term')]/following::p[1] | //*[contains(@class,'layterm') or contains(@id,'layterm')]")
                            for c in candidates:
                                try:
                                    t = " ".join(c.text.split()).strip()
                                    if t and t.lower() != "upcoming & historical information":
                                        lay_txt = t
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass
                    data["lay_term"] = lay_txt or ""
                except Exception as e:
                    logger.debug("CPT layterm extraction exception: %s", e)
                    data["lay_term"] = ""
            # normalize
            if data.get("lay_term") is None:
                data["lay_term"] = ""
        except Exception as e:
            logger.debug("Lay term extraction exception: %s", e)
            data["lay_term"] = ""

        logger.info("Scraped code page: %s (url: %s)", data.get("code"), code_url)
    except Exception as e:
        logger.exception("scrape_code_page failed for %s: %s", code_url, e)

    return data

# ---------------- Search + open (HCPCS-aware) ----------------
def search_and_open_code(driver, code_token: str, code_type: str = "CPT") -> Optional[str]:
    logger.info("Searching %s code %s", code_type, code_token)
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

        if code_type.upper() == "HCPCS":
            try:
                el = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS["code_link_xpath"], timeout=2, condition="clickable")
                if el:
                    try:
                        driver.execute_script("arguments[0].click();", el)
                    except Exception:
                        try:
                            el.click()
                        except Exception:
                            pass
                    random_throttle(DEFAULT_THROTTLE)
                    dismiss_popups(driver)
                    time.sleep(0.6)
                    # click details if present
                    det = wait_for_optional(driver, By.XPATH, HCPCS_SELECTORS["details_click_xpath"], timeout=2, condition="clickable")
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
                    return driver.current_url
            except Exception:
                pass

        # Generic CPT / fallback flow
        if "/cpt-codes-range/" in cur or "/codes/cpt-codes-range/" in cur or "/hcpcs-codes-range/" in cur:
            logger.debug("Search landed on range page: %s", cur)
            det = wait_for_optional(driver, By.LINK_TEXT, "Details", timeout=4, condition="clickable")
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
                return driver.current_url

            try:
                anchors = driver.find_elements(By.XPATH, "//a[contains(translate(.,'details','DETAILS'),'DETAILS') or contains(@href,'/codes/')]")
                for a in anchors:
                    if a.is_displayed():
                        try:
                            driver.execute_script("arguments[0].click();", a)
                            random_throttle(DEFAULT_THROTTLE)
                            dismiss_popups(driver)
                            return driver.current_url
                        except Exception:
                            continue
            except Exception:
                pass

        try:
            wait_for(driver, By.XPATH, "//h1[contains(@class,'pull-left') or contains(@class,'page-title')]", timeout=8, condition="visible")
        except Exception:
            logger.debug("Code header not found after search; continuing anyway.")
        return driver.current_url
    except Exception:
        logger.exception("search_and_open_code failed for %s", code_token)
        return None

# ---------------- Click NEXT ----------------
def click_next_code(driver) -> bool:
    try:
        cur = driver.current_url
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

# ---------------- Main loop: search -> scrape -> next until stop ----------------
def run_search_to_stop_loop(driver, start_code: str, stop_code: str, checkpoint_path: Path, output_dir: Path, throttle: float = DEFAULT_THROTTLE, max_cycles: int = 2000, code_type: str = "CPT"):
    logger.info("run_search_to_stop_loop start=%s stop=%s type=%s", start_code, stop_code, code_type)
    try:
        first_url = search_and_open_code(driver, start_code, code_type=code_type)
        if not first_url:
            logger.error("Could not open start code %s", start_code)
            return

        cycles = 0
        prev_page_marker = None

        while cycles < max_cycles:
            cycles += 1
            try:
                cur_url = driver.current_url
                rec = scrape_code_page(driver, cur_url, code_range=start_code, code_type=code_type)
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

# ---------------- CLI / main ----------------
def main():
    parser = argparse.ArgumentParser(description="AAPC Codify CPT/HCPCS search->scrape->next scraper")
    parser.add_argument("--headless", action="store_true", default=DEFAULT_HEADLESS, help="Run Chrome headless (default True)")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run with UI")
    parser.add_argument("--year", default=DEFAULT_YEAR)
    parser.add_argument("--output-dir", default="./outputs")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE)
    parser.add_argument("--allow-placeholder", action="store_true", help="Allow placeholder credentials")
    parser.add_argument("--start-code", type=str, required=True, help="Start CPT/HCPCS code to search and begin scraping from")
    parser.add_argument("--stop-code", type=str, default="", help="Stop code (inclusive); leave empty to run until navigation stops")
    parser.add_argument("--code-type", type=str, choices=["CPT", "HCPCS"], default="CPT",
                        help="Type of code you are scraping (default CPT). Use HCPCS for J-codes.")
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
        # login (reuse your existing login flow)
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

                try:
                    user_el = wait_for(driver, By.ID, "userProvidedSignInName", timeout=20, condition="visible")
                    user_el.clear()
                    user_el.send_keys(username)
                    next_btn = wait_for(driver, By.ID, "next", timeout=10, condition="clickable")
                    next_btn.click()
                except TimeoutException:
                    logger.exception("Username field or continue button not found during login.")
                    return False

                try:
                    pw_el = wait_for(driver, By.ID, "password", timeout=15, condition="visible")
                    pw_el.clear()
                    pw_el.send_keys(password)
                    cont = wait_for(driver, By.ID, "continue", timeout=10, condition="clickable")
                    cont.click()
                except TimeoutException:
                    logger.exception("Password field or continue button not found during login.")
                    return False

                try:
                    wait_for(driver, By.XPATH, "//a[contains(@href,'/codes/all_coding_tools/home')]", timeout=25, condition="presence")
                    logger.info("Login seems successful (codify link present).")
                    return True
                except TimeoutException:
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

        logged = login_to_aapc(driver, username, password, allow_placeholder=args.allow_placeholder, max_retries=args.max_retries)
        if not logged:
            logger.error("Login failed. Exiting.")
            try:
                driver.quit()
            except Exception:
                pass
            sys.exit(2)

        # try to skip migrate modal
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

        # ensure year
        try:
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

            ensure_year_selected(driver, args.year)
        except Exception:
            pass

        # kick off search->scrape->next
        run_search_to_stop_loop(driver, args.start_code.strip(), args.stop_code.strip(), checkpoint_path, output_dir, throttle=args.throttle, code_type=args.code_type)

        logger.info("Finished main run.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()



