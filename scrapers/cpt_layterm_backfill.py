

from __future__ import annotations
import argparse
import logging
import os
import random
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    StaleElementReferenceException,
    ElementNotInteractableException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

import pandas as pd

# Config
DEFAULT_YEAR = "2025"
DEFAULT_HEADLESS = True
DEFAULT_THROTTLE = 0.5
SEARCH_INPUT_ID = "top_search"
LAYTERM_LINK_TEXT = "Lay Term"


CHROMEDRIVER_PATH = r".\chromedriver\chromedriver-win64\chromedriver.exe"

# Logging 
logger = logging.getLogger("aapc_excel_scraper")
logger.setLevel(logging.INFO)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_console)

# Utilities 
def now_ts_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def random_throttle(base: float) -> None:
    time.sleep(base + random.random() * base)

#Selenium driver builder 
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

    if CHROMEDRIVER_PATH is None:
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            logger.info("Using webdriver-manager to auto-download chromedriver")
        except ImportError:
            logger.error("webdriver-manager not installed. Install it with: pip install webdriver-manager")
            raise
    else:
        if not os.path.exists(CHROMEDRIVER_PATH):
            logger.error(f"Chromedriver not found at: {CHROMEDRIVER_PATH}")
            raise FileNotFoundError(f"Chromedriver not found: {CHROMEDRIVER_PATH}")
        service = Service(CHROMEDRIVER_PATH)
        logger.info(f"Using chromedriver at: {CHROMEDRIVER_PATH}")

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

# Wait helpers 
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

def dismiss_popups(driver) -> None:
    try:
        candidates = [
            ("xpath", "//button[contains(.,'Skip') or contains(.,'Close') or contains(.,'×') or contains(.,'No Thanks') or contains(.,'Not Now')]"),
            ("css selector", "button[aria-label='Close']"),
            ("css selector", "button.btn.btn-primary.skip-btn"),
            ("xpath", "//div[contains(@class,'modal')]//button[contains(.,'Close') or contains(.,'OK') or contains(.,'Skip')]"),
        ]
        for by, sel in candidates:
            try:
                els = driver.find_elements(getattr(By, by.upper()), sel)
                for el in els:
                    if el.is_displayed():
                        try:
                            driver.execute_script("arguments[0].click();", el)
                            logger.debug("Clicked popup close/skip")
                            time.sleep(0.4)
                        except Exception:
                            pass
            except Exception:
                continue
    except Exception as e:
        logger.debug("dismiss_popups failed: %s", e)

def expand_layterm_readmore(driver, max_clicks: int = 4, wait_after_click: float = 0.7) -> bool:
    """
    Specifically expand the Lay Term 'Read More' anchor(s) that call readMoreLaytermFunc(...).
    Returns True if #fullLayterm becomes visible (expanded), False otherwise.
    """
    try:
        # XPath that matches read-more anchors that call readMoreLaytermFunc(...)
        readmore_xpath = "//a[contains(@onclick,'readMoreLaytermFunc') and (normalize-space(.)='Read More' or contains(normalize-space(.),'Read More') or contains(normalize-space(.),'Read more'))]"

        for i in range(max_clicks):
            
            try:
                full = driver.find_element(By.ID, "fullLayterm")
              
                displayed = driver.execute_script("return window.getComputedStyle(arguments[0]).display !== 'none' && arguments[0].offsetParent !== null;", full)
                if displayed:
                    return True
            except Exception:
                
                pass

            # find read more and click them
            elems = driver.find_elements(By.XPATH, readmore_xpath)
            if not elems:
                
                elems = driver.find_elements(By.XPATH, "//a[contains(@onclick,'readMoreLaytermFunc')]")

            if not elems:
                
                break

            clicked_any = False
            for el in elems:
                try:
                    
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    onclick = el.get_attribute("onclick") or ""
                    if onclick:
                       
                        try:
                            driver.execute_script(onclick)
                        except Exception:
                            
                            try:
                                driver.execute_script("arguments[0].click();", el)
                            except Exception:
                                el.click()
                    else:
                      
                        try:
                            driver.execute_script("arguments[0].click();", el)
                        except Exception:
                            try:
                                el.click()
                            except Exception:
                                pass
                    clicked_any = True
                    time.sleep(wait_after_click + random.random() * 0.4)
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue

            if not clicked_any:
                break

        
        try:
            full = driver.find_element(By.ID, "fullLayterm")
            visible = driver.execute_script("return window.getComputedStyle(arguments[0]).display !== 'none' && arguments[0].offsetParent !== null;", full)
            return bool(visible)
        except Exception:
            return False

    except Exception as e:
        logger.debug(f"expand_layterm_readmore error: {e}")
        return False


# Login 
def login_to_aapc(driver, username: str, password: str, max_retries: int = 3) -> bool:
    login_url = "https://www.aapc.com/"
    try:
        driver.get(login_url)
    except WebDriverException:
        logger.exception("Failed to open aapc.com login page")
        return False

    try:
       
        try:
            sign_in = wait_for(driver, By.ID, "btnSignIn", timeout=10, condition="clickable")
            driver.execute_script("arguments[0].click();", sign_in)
        except TimeoutException:
            try:
                link = wait_for(driver, By.XPATH, "//a[contains(text(),'Sign In')]", timeout=5, condition="clickable")
                driver.execute_script("arguments[0].click();", link)
            except Exception:
                logger.exception("Sign In element not found/clickable")
                return False

        # Enter username
        try:
            user_el = wait_for(driver, By.ID, "userProvidedSignInName", timeout=20, condition="visible")
            user_el.clear()
            user_el.send_keys(username)
            next_btn = wait_for(driver, By.ID, "next", timeout=10, condition="clickable")
            driver.execute_script("arguments[0].click();", next_btn)
        except TimeoutException:
            logger.exception("Username field or continue button not found during login.")
            return False

        # Enter password
        try:
            pw_el = wait_for(driver, By.ID, "password", timeout=15, condition="visible")
            pw_el.clear()
            pw_el.send_keys(password)
            cont = wait_for(driver, By.ID, "continue", timeout=10, condition="clickable")
            driver.execute_script("arguments[0].click();", cont)
        except TimeoutException:
            logger.exception("Password field or continue button not found during login.")
            return False

        # Confirm landing
        try:
            wait_for(driver, By.XPATH, "//a[contains(@href,'/codes/all_coding_tools/home')]", timeout=25, condition="presence")
            logger.info("Login successful")
            return True
        except TimeoutException:
            try:
                wait_for(driver, By.XPATH, "//a[contains(@href,'/account/dashboard.aspx')]", timeout=10)
                logger.info("Login landed on dashboard")
                return True
            except Exception:
                logger.error("Login did not reach expected page")
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
            select = Select(sel_elem)
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
            logger.warning("Could not select year")
    except Exception as e:
        logger.warning("ensure_year_selected error: %s", e)

# Expand Read More helper 
def expand_all_read_more(driver, xpath_or_element: Union[str, 'webdriver.remote.webelement.WebElement'], max_attempts: int = 4, wait_after_click: float = 0.9) -> int:
    clicked_total = 0

    for attempt in range(1, max_attempts + 1):
        try:
            if isinstance(xpath_or_element, str):
                try:
                    elements = driver.find_elements(By.XPATH, xpath_or_element)
                except Exception:
                    elements = []
            else:
                scope = xpath_or_element
                try:
                    elements = scope.find_elements(By.XPATH, ".//a[contains(.,'Read More') or contains(.,'Read more') or contains(@onclick,'readMoreLaytermFunc') or contains(@onclick,'readMore') or contains(@class,'read-more') or contains(.,'... Read More')]")
                except Exception:
                    elements = []

            if not elements:
                try:
                    elements = driver.find_elements(By.XPATH, "//a[contains(translate(.,'READ MORE','read more'),'read more') or contains(@onclick,'readMoreLaytermFunc') or contains(@class,'read-more')]")
                except Exception:
                    elements = []

            if not elements:
                if attempt == 1:
                    logger.debug("expand_all_read_more: no Read More elements found on attempt 1")
                break

            clicked_this_round = 0
            for el in elements:
                try:
                    if not el.is_displayed():
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        except Exception:
                            pass

                    onclick_code = None
                    try:
                        onclick_code = el.get_attribute("onclick")
                    except Exception:
                        onclick_code = None

                    if onclick_code:
                        m = re.search(r"readMoreLaytermFunc\(([\d, ]+)\)", onclick_code or "")
                        if m:
                            try:
                                driver.execute_script(onclick_code)
                                clicked_this_round += 1
                                clicked_total += 1
                                logger.debug("Executed onclick JS (readMoreLaytermFunc) for read more")
                                time.sleep(wait_after_click)
                                continue
                            except Exception:
                                pass
                        try:
                            driver.execute_script(onclick_code)
                            clicked_this_round += 1
                            clicked_total += 1
                            logger.debug("Executed onclick JS for read more (onclick attr)")
                            time.sleep(wait_after_click)
                            continue
                        except Exception:
                            pass

                    try:
                        el.click()
                        clicked_this_round += 1
                        clicked_total += 1
                        logger.debug("Clicked Read More (element.click())")
                        time.sleep(wait_after_click)
                        continue
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", el)
                            clicked_this_round += 1
                            clicked_total += 1
                            logger.debug("Clicked Read More (driver.execute_script click)")
                            time.sleep(wait_after_click)
                            continue
                        except Exception as e:
                            logger.debug(f"Failed to click Read More element: {e}")
                            continue

                except StaleElementReferenceException:
                    logger.debug("StaleElementReference while clicking read more; will retry in next attempt")
                    continue
                except Exception as e:
                    logger.debug(f"Unexpected error while clicking Read More: {e}")
                    continue

            if clicked_this_round > 0:
                time.sleep(0.6 + random.random() * 0.6)
                continue
            else:
                break

        except Exception as e:
            logger.debug(f"expand_all_read_more attempt {attempt} error: {e}")
            time.sleep(0.3 + random.random() * 0.4)
            continue

    if clicked_total:
        logger.info(f"expand_all_read_more: expanded {clicked_total} 'Read More' element(s)")
        time.sleep(0.4 + random.random() * 0.8)
    else:
        logger.debug("expand_all_read_more: nothing expanded")

    return clicked_total

# Search and extract lay term 
def search_and_extract_layterm(driver, cpt_code: str, throttle: float = DEFAULT_THROTTLE) -> str:
    """
    Robust search -> deterministic navigation -> ensure Lay Term tab open -> expand Read More -> extract lay term.
    Replaces original. Adds strict wait-for-detail verification after ANY navigation (Details click, code click, direct URL).
    Saves failure HTML + PNG to ./results for debugging if extraction ultimately fails.
    """
    logger.info(f"Processing CPT code: {cpt_code}")
    attempts_for_code = 2
    last_error = None
    code_text = str(cpt_code).lstrip("0").strip()

    # helper: wait until current page looks like the code detail page
    def wait_for_detail_page(timeout: float = 8.0) -> bool:
        """Return True if page URL or H1 contains the code_text within timeout, else False."""
        end = time.time() + timeout
        seen = set()
        while time.time() < end:
            try:
                cur_url = (driver.current_url or "").lower()
            except Exception:
                cur_url = ""
            h1_text = ""
            try:
                h1_el = wait_for_optional(driver, By.XPATH, "//h1", timeout=1, condition="presence")
                if h1_el:
                    try:
                        h1_text = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", h1_el) or ""
                    except Exception:
                        h1_text = h1_el.text or ""
                h1_text = h1_text.strip().lower()
            except Exception:
                h1_text = ""
            if code_text.lower() in cur_url or code_text.lower() in h1_text:
                logger.debug(f"Detail page verified for {code_text} (url contains or h1 contains). URL='{cur_url[:200]}', h1='{h1_text[:120]}'")
                return True
            # small sleep and re-check
            time.sleep(0.35)
        # final debug log
        try:
            cur_url = (driver.current_url or "")[:400]
        except Exception:
            cur_url = "<unable to read current_url>"
        logger.debug(f"wait_for_detail_page timed out for {code_text}. last url='{cur_url}' h1='{h1_text[:120]}'")
        return False

    # helper: try deterministic extraction on current page (activates Lay Term tab, expands read more)
    def try_extract_from_current_page() -> str:
        try:
            # call page function to open tab if available
            try:
                driver.execute_script("if(typeof showadditionalInfo === 'function'){ showadditionalInfo('cpt_layterm'); }")
            except Exception:
                pass

            # click Lay Term tab anchor if present
            try:
                lay_tab_xpath = "//a[@data-toggle='tab' and @href='#cpt_layterm' and contains(normalize-space(.),'Lay Term')]"
                lay_tab = wait_for_optional(driver, By.XPATH, lay_tab_xpath, timeout=3, condition="clickable")
                if lay_tab:
                    try:
                        driver.execute_script("arguments[0].click();", lay_tab)
                    except Exception:
                        try:
                            lay_tab.click()
                        except Exception:
                            pass
            except Exception:
                pass

            # wait for the container to be present
            try:
                wait_for(driver, By.ID, "cpt_layterm", timeout=6, condition="presence")
            except Exception:
                # continue - sometimes content appears lazily
                time.sleep(0.5)

            # verify we are indeed on the expected detail page
            if not wait_for_detail_page(timeout=4):
           
                logger.debug(f"Current page does not verify as detail page for {code_text}; skipping extraction on this DOM.")
                return ""

           
            try:
                expand_layterm_readmore(driver, max_clicks=5, wait_after_click=0.6)
            except Exception:
                pass

           
            try:
                full_el = wait_for_optional(driver, By.ID, "fullLayterm", timeout=2, condition="presence")
                if full_el:
                    visible = driver.execute_script(
                        "return window.getComputedStyle(arguments[0]).display !== 'none' && arguments[0].offsetParent !== null;", full_el
                    )
                    if visible:
                        txt = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", full_el) or ""
                        txt_norm = " ".join(txt.split()).strip()
                        if txt_norm and len(txt_norm) > 6:
                            return txt_norm
            except Exception:
                pass

           
            try:
                readmore = wait_for_optional(driver, By.ID, "readMoreLayterm", timeout=2, condition="presence")
                if readmore:
                    txt = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", readmore) or ""
                    txt_norm = " ".join(txt.split()).strip()
                    if txt_norm and len(txt_norm) > 6:
                        return txt_norm
            except Exception:
                pass

            # final fallback: whole cpt_layterm node
            try:
                cpt_node = wait_for_optional(driver, By.ID, "cpt_layterm", timeout=2, condition="presence")
                if cpt_node:
                    txt = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", cpt_node) or ""
                    txt_norm = " ".join(txt.split()).strip()
                    if txt_norm and len(txt_norm) > 6:
                        return txt_norm
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"try_extract_from_current_page error for {code_text}: {e}")
        return ""

  
    search_selectors = [
        (By.ID, SEARCH_INPUT_ID),
        (By.CSS_SELECTOR, "input[type='search']"),
        (By.CSS_SELECTOR, "input[placeholder*='Search']"),
        (By.CSS_SELECTOR, "input[aria-label*='Search']"),
        (By.NAME, "q"),
        (By.NAME, "search"),
        (By.XPATH, "//input[contains(@class,'search') or contains(@id,'search') or contains(@placeholder,'Search')]"),
    ]

    for attempt in range(1, attempts_for_code + 1):
        try:
            dismiss_popups(driver)
            ensure_correct_year(driver, DEFAULT_YEAR)
            random_throttle(0.2)

            # find search input
            search_el = None
            for by, sel in search_selectors:
                try:
                    cand = wait_for_optional(driver, by, sel, timeout=2, condition="presence")
                    if cand and cand.is_displayed():
                        search_el = cand
                        break
                except Exception:
                    continue

            if search_el:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].focus(); arguments[0].value='';", search_el)
                except Exception:
                    pass
                # set value via JS to be robust
                try:
                    driver.execute_script(
                        "const el=arguments[0]; el.value=arguments[1]; el.dispatchEvent(new Event('input',{bubbles:true}));",
                        search_el, code_text
                    )
                    # Press Enter via JS key event if needed
                    driver.execute_script("arguments[0].dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));", search_el)
                except Exception:
                    try:
                        search_el.clear()
                        search_el.send_keys(code_text)
                        search_el.send_keys(Keys.ENTER)
                    except Exception:
                        pass
                time.sleep(0.9 + random.random() * 0.6)
            else:
                logger.debug("Search input not found on page; will try Details/code link or direct URL later.")

            random_throttle(throttle)
            dismiss_popups(driver)
            time.sleep(0.4)

            # Try 'Details' link firs
            try:
                details_xpath_code = f"//a[contains(@href,'/cpt-codes/{code_text}') and normalize-space(.)='Details']"
                details_link = wait_for_optional(driver, By.XPATH, details_xpath_code, timeout=2, condition="clickable")
                if not details_link:
                    details_link = wait_for_optional(driver, By.XPATH, "//a[normalize-space(.)='Details' and contains(@href,'/cpt-codes/')]", timeout=2, condition="clickable")
                if details_link:
                    logger.debug(f"Clicking Details link for {code_text}")
                    try:
                        driver.execute_script("arguments[0].click();", details_link)
                    except Exception:
                        try:
                            details_link.click()
                        except Exception:
                            pass
                    # wait until detail page actually loads
                    if not wait_for_detail_page(timeout=7):
                        logger.debug(f"Details click did not land on expected detail for {code_text}")
                    else:
                        # now try extraction
                        res = try_extract_from_current_page()
                        if res:
                            logger.info(f"Extracted lay term for {cpt_code} via Details (len={len(res)})")
                            return res
            except Exception as e:
                logger.debug(f"Clicking 'Details' link failed for {code_text}: {e}")

            # Next try clicking the code link itself
            try:
                code_link = wait_for_optional(driver, By.LINK_TEXT, code_text, timeout=2, condition="clickable")
                if not code_link:
                    code_link = wait_for_optional(driver, By.PARTIAL_LINK_TEXT, code_text, timeout=2, condition="clickable")
                if code_link:
                    logger.debug(f"Clicking code link for {code_text}")
                    try:
                        driver.execute_script("arguments[0].click();", code_link)
                    except Exception:
                        try:
                            code_link.click()
                        except Exception:
                            pass
                    # wait for page to become the detail page
                    if not wait_for_detail_page(timeout=7):
                        logger.debug(f"Code link click did not land on expected detail for {code_text}")
                    else:
                        res = try_extract_from_current_page()
                        if res:
                            logger.info(f"Extracted lay term for {cpt_code} via code link (len={len(res)})")
                            return res
            except Exception as e:
                logger.debug(f"Clicking code link failed for {code_text}: {e}")

          
            res = try_extract_from_current_page()
            if res:
                logger.info(f"Extracted lay term for {cpt_code} on current page (len={len(res)})")
                return res

        except Exception as e:
            last_error = e
            logger.debug(f"Search-driven attempt {attempt} error for {code_text}: {e}")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            try:
                driver.refresh()
            except Exception:
                pass
            time.sleep(0.6 + random.random() * 0.6)
            continue

    # 2) Direct URL fallbacks (try canonical detail page)
    tried_urls = []
    direct_url_templates = [
        f"https://www.aapc.com/codes/cpt-codes/{code_text}/",
        f"https://www.aapc.com/codes/cpt-codes/{code_text}",
        f"https://www.aapc.com/codes/cpt-codes/?s={code_text}",
    ]
    for url in direct_url_templates:
        try:
            logger.info(f"Trying direct URL for {cpt_code}: {url}")
            tried_urls.append(url)
            driver.get(url)
            # wait for nav/load
            time.sleep(0.8 + random.random() * 0.8)
            dismiss_popups(driver)
            if not wait_for_detail_page(timeout=6):
                logger.debug(f"Direct URL {url} did not show detail for {code_text}")
                continue
            res = try_extract_from_current_page()
            if res:
                logger.info(f"Extracted lay term for {cpt_code} via direct URL (len={len(res)})")
                return res
        except Exception as e:
            last_error = e
            logger.debug(f"Direct URL attempt {url} error: {e}")
            continue

    # 3) Exhausted: save robust failure artifacts and return empty
    try:
        os.makedirs("./results", exist_ok=True)
        dump_path = f"./results/failed_{code_text}.html"
        png_path = f"./results/failed_{code_text}.png"
        # Try to save page_source; if that fails, write a small debug HTML with current_url/h1
        try:
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        except Exception as e:
            logger.debug(f"Failed to write driver.page_source for {code_text}: {e}")
            # write minimal debug html
            try:
                cur_url = driver.current_url
            except Exception:
                cur_url = "<unable to read current_url>"
            try:
                h1_el = wait_for_optional(driver, By.XPATH, "//h1", timeout=1, condition="presence")
                h1_text = ""
                if h1_el:
                    try:
                        h1_text = driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", h1_el) or ""
                    except Exception:
                        h1_text = h1_el.text or ""
            except Exception:
                h1_text = ""
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(f"<html><body><h2>Debug dump failed to get page_source</h2><p>current_url: {cur_url}</p><pre>h1: {h1_text}</pre></body></html>")

        # Try to save screenshot (useful visual debugging)
        try:
            driver.save_screenshot(png_path)
        except Exception as e:
            logger.debug(f"Saving screenshot for {code_text} failed: {e}")

        logger.warning(f"Saved failed artifacts for {code_text} to {dump_path} and {png_path}. Tried URLs: {tried_urls}")
    except Exception as e:
        logger.debug(f"Failed to write failure artifacts for {code_text}: {e}")

    if last_error:
        logger.error(f"Exhausted retries for {code_text}. Last error: {last_error}")
    else:
        logger.error(f"Exhausted retries for {code_text}. No lay term found; failure artifacts saved.")
    return ""

# ---------------- Main processing function (writes XLSX + CSV) ----------------
def process_excel_file(driver, input_file: str, output_file: str, cpt_column: str, throttle: float):
    logger.info(f"Reading CPT codes from: {input_file}")
    try:
        df = pd.read_excel(input_file)
        if len(df.columns) == 1 and str(df.columns[0]).isdigit():
            logger.info("Excel file has no header row, reading without header")
            df = pd.read_excel(input_file, header=None)
            df.columns = ['CPT_Code']
            cpt_column = 'CPT_Code'
        if cpt_column not in df.columns:
            logger.warning(f"Column '{cpt_column}' not found. Using first column instead.")
            first_col = df.columns[0]
            df = df.rename(columns={first_col: 'CPT_Code'})
            cpt_column = 'CPT_Code'
            logger.info(f"Renamed column '{first_col}' to 'CPT_Code'")
    except Exception as e:
        logger.error(f"Failed to read Excel file: {e}")
        return

    cpt_codes = df[cpt_column].dropna().astype(str).str.strip().unique()
    logger.info(f"Found {len(cpt_codes)} unique CPT codes to process")
    results = []

    # prepare output file paths
    out_xlsx = output_file if str(output_file).lower().endswith(".xlsx") else str(output_file) + ".xlsx"
    out_csv = out_xlsx[:-5] + ".csv"

    for idx, cpt_code in enumerate(cpt_codes, 1):
        try:
            base_search_url = "https://www.aapc.com/codes/cpt-codes/"
            driver.get(base_search_url)
            try:
                wait_for(driver, By.ID, SEARCH_INPUT_ID, timeout=10, condition="clickable")
            except Exception:
                # fallback wait for a likely header on listing page
                wait_for_optional(driver, By.XPATH, "//h1[contains(.,'CPT Codes') or contains(.,'CPT code')]", timeout=6)
            time.sleep(0.6 + random.random() * 0.6)
            dismiss_popups(driver)
        except Exception as e:
            logger.debug(f"Reset to base search page failed before processing {cpt_code}: {e}")

        logger.info(f"Processing {idx}/{len(cpt_codes)}: {cpt_code}")
        lay_term = search_and_extract_layterm(driver, cpt_code, throttle)
        results.append({
            'CPT_Code': cpt_code,
            'Lay_Term': lay_term,
            'Timestamp': now_ts_iso()
        })

        # Save intermediate results frequently
        # if idx % 5 == 0 or idx == len(cpt_codes):
        #     try:
        #         temp_df = pd.DataFrame(results)
        #         temp_xlsx = out_xlsx.replace('.xlsx', '_temp.xlsx')
        #         temp_csv = out_csv.replace('.csv', '_temp.csv')
        #         temp_df.to_excel(temp_xlsx, index=False)
        #         temp_df.to_csv(temp_csv, index=False, encoding='utf-8')
        #         logger.info(f"Saved intermediate results to {temp_xlsx} and {temp_csv}")
        #     except Exception as e:
        #         logger.exception(f"Failed to save intermediate results: {e}")
                # Save after every CPT code (immediate persistence)
        try:
            temp_df = pd.DataFrame(results)
            # atomic-ish write: write to temp then rename
            temp_xlsx = out_xlsx.replace('.xlsx', '_temp.xlsx')
            temp_csv = out_csv.replace('.csv', '_temp.csv')
            temp_df.to_excel(temp_xlsx, index=False)
            temp_df.to_csv(temp_csv, index=False, encoding='utf-8')
            # replace final filenames (overwrite)
            os.replace(temp_xlsx, out_xlsx)
            os.replace(temp_csv, out_csv)
            logger.info(f"Saved progress {idx}/{len(cpt_codes)} to {out_xlsx} and {out_csv}")
        except Exception as e:
            logger.exception(f"Failed to save progress at {idx}: {e}")

    # Save final results to both XLSX and CSV
    try:
        results_df = pd.DataFrame(results)
        # ensure output folder exists
        out_dir = os.path.dirname(out_xlsx) or "."
        os.makedirs(out_dir, exist_ok=True)
        results_df.to_excel(out_xlsx, index=False)
        results_df.to_csv(out_csv, index=False, encoding='utf-8')
        logger.info(f"Saved final results to {out_xlsx} and {out_csv}")
    except Exception as e:
        logger.exception(f"Failed to save final results: {e}")

    logger.info(f"Processed {len(results)} CPT codes")

# ---------------- Year ensure ----------------
def ensure_correct_year(driver, target_year: str = DEFAULT_YEAR):
    try:
        select_elems = driver.find_elements(By.TAG_NAME, "select")
        for sel in select_elems:
            try:
                select = Select(sel)
                selected = select.first_selected_option.text
                if target_year not in selected:
                    try:
                        select.select_by_visible_text(target_year)
                    except Exception:
                        try:
                            select.select_by_value(target_year)
                        except Exception:
                            try:
                                select.select_by_index(0)
                            except Exception:
                                pass
                    logger.info(f"Switched code set year back to {target_year}")
                    time.sleep(1)
                    return
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"ensure_correct_year failed: {e}")


# ---------------- CLI / main ----------------

def main():
    parser = argparse.ArgumentParser(description="AAPC CPT Lay Term Scraper - Excel Based (robust) - saves xlsx + csv")
    parser.add_argument("--input-file", required=True, help="Path to input Excel file with CPT codes")
    parser.add_argument("--output-file", default="./layterm_results.xlsx", help="Path to output Excel file (CSV will be auto-created alongside)")
    parser.add_argument("--cpt-column", default="CPT_Code", help="Name of column containing CPT codes (default: CPT_Code)")
    parser.add_argument("--headless", action="store_true", default=DEFAULT_HEADLESS, help="Run Chrome headless")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run with UI")
    parser.add_argument("--year", default=DEFAULT_YEAR, help="Code set year")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE, help="Throttle delay between requests")
    parser.add_argument("--username", default="YOUR_AAPC_USERNAME", help="AAPC username")
    parser.add_argument("--password", default="YOUR_AAPC_PASSWORD", help="AAPC password")
    args = parser.parse_args()

    logger.setLevel(getattr(logging, args.log_level.upper(), logging.INFO))

    if not os.path.exists(args.input_file):
        logger.error(f"Input file not found: {args.input_file}")
        sys.exit(1)

    driver = build_driver(args.headless)
    logger.info(f"Driver created (headless={args.headless})")

    try:
        logged = login_to_aapc(driver, args.username, args.password)
        if not logged:
            logger.error("Login failed. Exiting.")
            sys.exit(2)

        try:
            skip_btn = wait_for_optional(driver, By.XPATH, "//form[@id='migrate_user_terms']/div[2]/button[2]", timeout=5, condition="clickable")
            if skip_btn:
                try:
                    driver.execute_script("arguments[0].click();", skip_btn)
                except Exception:
                    skip_btn.click()
                logger.info("Clicked Skip on migrate modal")
        except Exception:
            pass

        ensure_year_selected(driver, args.year)

        process_excel_file(driver, args.input_file, args.output_file, args.cpt_column, args.throttle)

        logger.info("Processing complete!")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()


