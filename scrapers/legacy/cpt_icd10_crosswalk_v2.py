import os
import json
import time
import logging
import threading
import pandas as pd
import openpyxl
import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------------------------ Logger Setup ------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('app_1.log')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ------------------------------ Globals ------------------------------
threadLocal = threading.local()
current_directory = os.getcwd()
ERAPATH = os.path.join(current_directory, 'Config')
os.makedirs(ERAPATH, exist_ok=True)

# ------------------------------ Driver Setup ------------------------------
def get_driver():
    driver = getattr(threadLocal, 'driver', None)
    if driver is None:
        options = webdriver.ChromeOptions()
        settings = {
            "recentDestinations": [{"id": "Save as PDF", "origin": "local"}],
            "selectedDestinationId": "Save as PDF",
            "version": 2,
            "isHeaderFooterEnabled": False
        }
        prefs = {
            "download.default_directory": ERAPATH,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "printing.print_preview_sticky_settings.appState": json.dumps(settings)
        }
        options.add_experimental_option("prefs", prefs)
        options.add_argument('--start-maximized')
        options.add_argument('--kiosk-printing')
        driver = webdriver.Chrome(options=options)
        setattr(threadLocal, 'driver', driver)
    return driver

# ------------------------------ Helper Functions ------------------------------
def wait_clickable(driver, xpath, timeout=30):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))

def check_element_loaded(driver, xpath):
    try:
        WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, xpath)))
        return True
    except:
        return False
    
def save_to_excel(data_list, file_name):
    """
    Saves a list of data (in dictionary format) to Excel.
    Collects the data first and saves to the Excel file once after all processing.

    :param data_list: List of dictionaries to save.
    :param file_name: Excel file to save the data.
    """
    # Convert the list of dicts to a DataFrame
    new_df = pd.DataFrame(data_list)

    if os.path.exists(file_name):
        old_df = pd.read_excel(file_name)
        df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        df = new_df

    # Save the combined DataFrame to Excel
    df.to_excel(file_name, index=False)
    logger.info(f"Data saved to {file_name}")


# # ------------------------------ CPT Processing Logic ------------------------------
# def process_cpt(driver, cpt_code, count):
#     try:
#         driver.switch_to.default_content()
#         driver.switch_to.window(driver.window_handles[0])

#         search_input = None
#         try:
#             search_input = wait_clickable(driver, '//input[@id="top_search"]', timeout=2)
#         except:
#             try:
#                 close_button = wait_clickable(driver, '//*[@id="autoFavoriteModal"]/div/div/div[2]/div/div[2]/button', timeout=2)
#                 close_button.click()
#                 search_input = wait_clickable(driver, '//input[@id="top_search"]', timeout=2)
#             except:
#                 search_input = wait_clickable(driver, '//*[@id="googleInput1"]', timeout=2)

#         search_input.click()
#         search_input.clear()
#         search_input.send_keys(cpt_code)
#         search_input.send_keys(Keys.ENTER)

#         time.sleep(2)

#         header_xpath = '//h1[@class="pull-left"]'
#         desc_xpaths = {
#             "HCPCS": '//div[@id="collapse_cdesc"]//div[@class="tab-pane"]',
#             "CPT": '//div[@id="collapse_desc_info"]//div[@class="tab-pane"]',
#         }

#         extracted_text = ''
#         if check_element_loaded(driver, header_xpath):
#             extracted_text = driver.find_element(By.XPATH, header_xpath).text.strip()

#         for key, path in desc_xpaths.items():
#             if key in extracted_text:
#                 # try:
#                 el = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.XPATH, path)))
#                 extracted_text_cpt = el.get_attribute('value') or el.text
#                 #     save_to_excel({"cpt": [cpt_code], "text": [extracted_text_cpt]}, 'cpt_extracted_data1.xlsx')
#                 #     return
#                 # except:
#                 #     break
#                 # After clicking ICD-10 CM tab
#                 filter_div_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/div'
#                 if check_element_loaded(driver, filter_div_xpath):
#                     filter_div = driver.find_element(By.XPATH, filter_div_xpath)
#                     filters = filter_div.find_elements(By.XPATH, './/a[contains(@class, "ab_links")]')

#                     for f in filters:
#                         try:
#                             driver.execute_script("arguments[0].click();", f)
#                             time.sleep(2)

#                             table_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/table'
#                             if check_element_loaded(driver, table_xpath):
#                                 rows = driver.find_elements(By.XPATH, f'{table_xpath}/tbody/tr')

#                                 for row in rows:
#                                     try:
#                                         cols = row.find_elements(By.TAG_NAME, 'td')
#                                         if len(cols) >= 2:
#                                             icd_code = cols[0].text.strip()
#                                             icd_desc = cols[1].text.strip()
#                                             save_to_excel(
#                                                 {"cpt": [cpt_code], "icd_code": [icd_code], "icd_desc": [icd_desc]},
#                                                 'cpt_icd10_detailed_data.xlsx'
#                                             )
#                                     except Exception as row_e:
#                                         logger.warning(f"Row extraction failed: {row_e}")
#                             else:
#                                 logger.warning(f"ICD-10 table not found for filter {f.text}")
#                         except Exception as filter_e:
#                             logger.warning(f"Filter {f.text} click failed: {filter_e}")


#         save_to_excel({"cpt": [cpt_code], "text": [""]}, 'CPT_failed_data1.xlsx')

#     except Exception as e:
#         exc_type, _, exc_tb = sys.exc_info()
#         logger.error(f"Line {exc_tb.tb_lineno}: {e}")
#         try:
#             Alert(driver).dismiss()
#         except:
#             pass
#         save_to_excel({"cpt": [cpt_code], "text": [""]}, 'CPT_failed_data1.xlsx')
# def process_cpt(driver, cpt_code, count):
#     all_data = []  # List to accumulate data before saving to Excel
    
#     try:
#         logger.info(f"Process started for {cpt_code} and Count is {count}")

#         driver.switch_to.default_content()
#         driver.switch_to.window(driver.window_handles[0])

#         # Try different search input xpaths
#         search_input = None
#         try:
#             search_input = wait_clickable(driver, '//input[@id="top_search"]', timeout=2)
#         except:
#             try:
#                 close_button = wait_clickable(driver, '//*[@id="autoFavoriteModal"]/div/div/div[2]/div/div[2]/button', timeout=2)
#                 close_button.click()
#                 search_input = wait_clickable(driver, '//input[@id="top_search"]', timeout=2)
#             except:
#                 search_input = wait_clickable(driver, '//*[@id="googleInput1"]', timeout=2)

#         search_input.click()
#         search_input.clear()
#         search_input.send_keys(cpt_code)
#         search_input.send_keys(Keys.ENTER)

#         time.sleep(2)

#         # Check if "Crosswalks" section is present
#         if not check_element_loaded(driver, '//*[@id="crosswalks"]'):
#             logger.info(f"No crosswalk section for CPT {cpt_code}")
#             all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
#             return save_to_excel(all_data, 'cpt_icd10_detailed_data.xlsx')

#         # Click the ICD-10 tab
#         try:
#             icd10_tab = wait_clickable(driver, '//*[@id="cpt_cross_icd10"]/a', timeout=5)
#             icd10_tab.click()
#             time.sleep(2)
#         except Exception as tab_click_error:
#             logger.warning(f"ICD-10 tab not clickable for CPT {cpt_code}: {tab_click_error}")
#             all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
#             return save_to_excel(all_data, 'cpt_icd10_detailed_data.xlsx')

#         # Wait for filters and loop through them
#         filter_div_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/div'
#         if check_element_loaded(driver, filter_div_xpath):
#             filter_div = driver.find_element(By.XPATH, filter_div_xpath)

#             # Get the list of texts or hrefs of all filters
#             filter_links = filter_div.find_elements(By.XPATH, './/a[contains(@class, "ab_links")]')
#             filter_texts = [f.text for f in filter_links]  # or f.get_attribute("onclick") if needed

#             for f_text in filter_texts:
#                 try:
#                     # Re-fetch the element by text each time (to avoid staleness)
#                     filter_div = driver.find_element(By.XPATH, filter_div_xpath)
#                     fresh_filter = filter_div.find_element(By.XPATH, f'.//a[contains(text(), "{f_text}")]')

#                     driver.execute_script("arguments[0].click();", fresh_filter)
#                     time.sleep(2)

#                     # **Optimization Here:** Extract the entire table at once for the selected filter
#                     table_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/table'
#                     if check_element_loaded(driver, table_xpath):
#                         table = driver.find_element(By.XPATH, table_xpath)
#                         rows = table.find_elements(By.XPATH, './tbody/tr')  # Extract all rows at once

#                         # Collecting column-wise data:
#                         icd_codes = []
#                         icd_descs = []
#                         for row in rows:
#                             try:
#                                 cols = row.find_elements(By.TAG_NAME, 'td')
#                                 if len(cols) >= 2:
#                                     icd_codes.append(cols[0].text.strip())
#                                     icd_descs.append(cols[1].text.strip())
#                             except Exception as row_e:
#                                 logger.warning(f"Row extraction failed for CPT {cpt_code}: {row_e}")

#                         # Add the column data to all_data
#                         for icd_code, icd_desc in zip(icd_codes, icd_descs):
#                             all_data.append({"cpt": cpt_code, "icd_code": icd_code, "icd_desc": icd_desc})

#                     else:
#                         logger.warning(f"ICD-10 table not found for filter {f_text} and CPT {cpt_code}")
#                 except Exception as filter_e:
#                     logger.warning(f"Filter {f_text} click failed for CPT {cpt_code}: {filter_e}")

#         else:
#             logger.warning(f"No ICD-10 filter section found for CPT {cpt_code}")
#             all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})

#     except Exception as e:
#         exc_type, _, exc_tb = sys.exc_info()
#         logger.error(f"Line {exc_tb.tb_lineno} - Error processing CPT {cpt_code}: {e}")
#         try:
#             Alert(driver).dismiss()
#         except:
#             pass
#         all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
    
#     # Save all collected data to Excel after processing
#     if all_data:
#         save_to_excel(all_data, 'cpt_icd10_detailed_data.xlsx')

def process_cpt(driver, cpt_code, count):
    all_data = []  # List to accumulate data before saving to Excel

    try:
        logger.info(f"Process started for {cpt_code} and Count is {count}")

        driver.switch_to.default_content()
        driver.switch_to.window(driver.window_handles[0])

        # === Handle any modal or overlay ===
        try:
            time.sleep(1)
            # Attempt to close modal if it exists
            close_btn = driver.find_elements(By.XPATH, '//div[@class="modal-body"]//button')
            for btn in close_btn:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
        except Exception as modal_e:
            logger.debug(f"No modal to close or failed closing modal: {modal_e}")

        # === Find and interact with search input ===
        search_input = None
        try:
            search_input = wait_clickable(driver, '//input[@id="top_search"]', timeout=3)
        except:
            try:
                # Fallback: alternate search input
                search_input = wait_clickable(driver, '//*[@id="googleInput1"]', timeout=3)
            except Exception as e:
                logger.error(f"Search input not found for CPT {cpt_code}: {e}")
                raise

        # Use JS click to avoid overlay click issues
        try:
            driver.execute_script("arguments[0].click();", search_input)
        except:
            search_input.click()
        try:
            search_input.clear()
            search_input.send_keys(cpt_code)
            search_input.send_keys(Keys.ENTER)
            time.sleep(2)

        except:
            try:
                modal_button_xpath = '//*[@id="autoFavoriteModal"]/div/div/div[2]/div/div[2]/button'
                modal_button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, modal_button_xpath))
                )
                driver.execute_script("arguments[0].click();", modal_button)
                logger.info(f"Closed Auto Favorite modal after searching CPT {cpt_code}")
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Unexpected error closing Auto Favorite modal: {e}")
            search_input = wait_clickable(driver, '//*[@id="googleInput1"]', timeout=3)
            search_input.clear()
            search_input.send_keys(cpt_code)
            search_input.send_keys(Keys.ENTER)
        
        time.sleep(2)

        # === Check for "Crosswalks" section ===
        if not check_element_loaded(driver, '//*[@id="crosswalks"]'):
            logger.info(f"No crosswalk section for CPT {cpt_code}")
            all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
            return save_to_excel(all_data, 'cpt_icd10_detailed_data_new_12April.xlsx')

        # === Click on ICD-10 tab ===
        try:
            icd10_tab = wait_clickable(driver, '//*[@id="cpt_cross_icd10"]/a', timeout=5)
            driver.execute_script("arguments[0].click();", icd10_tab)
            time.sleep(2)
        except Exception as tab_click_error:
            logger.warning(f"ICD-10 tab not clickable for CPT {cpt_code}: {tab_click_error}")
            all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
            return save_to_excel(all_data, 'cpt_icd10_detailed_data_new_12April.xlsx')

        # === Handle Filters and Extract Data ===
        filter_div_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/div'
        if check_element_loaded(driver, filter_div_xpath):
            filter_div = driver.find_element(By.XPATH, filter_div_xpath)
            filter_texts = [f.text for f in filter_div.find_elements(By.XPATH, './/a[contains(@class, "ab_links")]')]

            for f_text in filter_texts:
                try:
                    # Refresh DOM to avoid stale element exception
                    filter_div = driver.find_element(By.XPATH, filter_div_xpath)
                    fresh_filter = filter_div.find_element(By.XPATH, f'.//a[contains(text(), "{f_text}")]')
                    driver.execute_script("arguments[0].click();", fresh_filter)
                    time.sleep(2)

                    table_xpath = '//*[@id="icd10"]/div[1]/div[2]/div[1]/table'
                    if check_element_loaded(driver, table_xpath):
                        table = driver.find_element(By.XPATH, table_xpath)
                        rows = table.find_elements(By.XPATH, './tbody/tr')

                        icd_codes = []
                        icd_descs = []
                        for row in rows:
                            try:
                                cols = row.find_elements(By.TAG_NAME, 'td')
                                if len(cols) >= 2:
                                    icd_codes.append(cols[0].text.strip())
                                    icd_descs.append(cols[1].text.strip())
                            except Exception as row_e:
                                logger.warning(f"Row extraction failed for CPT {cpt_code}: {row_e}")

                        # Append collected data
                        for icd_code, icd_desc in zip(icd_codes, icd_descs):
                            all_data.append({"cpt": cpt_code, "icd_code": icd_code, "icd_desc": icd_desc})
                    else:
                        logger.warning(f"ICD-10 table not found for filter {f_text} and CPT {cpt_code}")
                except Exception as filter_e:
                    logger.warning(f"Filter {f_text} click failed for CPT {cpt_code}: {filter_e}")
        else:
            logger.warning(f"No ICD-10 filter section found for CPT {cpt_code}")
            all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})

    except Exception as e:
        exc_type, _, exc_tb = sys.exc_info()
        logger.error(f"Line {exc_tb.tb_lineno} - Error processing CPT {cpt_code}: {e}")
        try:
            Alert(driver).dismiss()
        except:
            pass
        all_data.append({"cpt": cpt_code, "icd_code": "", "icd_desc": ""})
        try:
            modal_button_xpath = '//*[@id="autoFavoriteModal"]/div/div/div[2]/div/div[2]/button'
            modal_button = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, modal_button_xpath))
            )
            driver.execute_script("arguments[0].click();", modal_button)
            logger.info(f"Closed Auto Favorite modal after searching CPT {cpt_code}")
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Unexpected error closing Auto Favorite modal: {e}")

    
    # === Save collected data to Excel ===
    if all_data:
        save_to_excel(all_data, 'cpt_icd10_detailed_data_new_12April.xlsx')


# ------------------------------ Main Automation Flow ------------------------------
def main():
    logger.info("Script started.")
    driver = get_driver()
    
    driver.maximize_window()

    url = "https://www.aapc.com/memberarea/my-purchased-items.aspx#2"
    username = "YOUR_AAPC_USERNAME"
    password = "YOUR_AAPC_PASSWORD"

    driver.get(url)

    # Login
    for field_xpath, value in [
        ('//*[@id="signInName"]', username),
        ('//*[@id="password"]', password),
    ]:
        field = wait_clickable(driver, field_xpath)
        field.clear()
        field.send_keys(value)

    wait_clickable(driver, '//button[@id="continue"]').click()

    # Dropdown click to navigate to Codify
    try:
        wait_clickable(driver, '//li[@id="pretoplogin"]//a[@id="linkSignOut"]').click()
        dropdown = wait_clickable(driver, '//*[@id="MyAAPCMenuItemTop"]/div/ul')
        if dropdown.is_displayed():
            wait_clickable(driver, '//*[@id="lnkUserLoggedInCodifySubscription"]/a').click()
    except Exception as e:
        logger.warning(f"Dropdown handling failed: {e}")
        try:
            wait_clickable(driver, '//li[@id="pretoplogin"]//a[@id="linkSignOut"]').click()
            wait_clickable(driver, '//*[@id="lnkUserLoggedInCodifySubscription"]/a').click()
        except Exception as inner_e:
            logger.error(f"Dropdown retry failed: {inner_e}")

    # Read CPT Codes
    try:
        df = pd.read_excel(r'cpt_not_macthing.xlsx')
    except:
        df = pd.read_excel(r'cpt_not_macthing.xlsx', engine='xlrd')

    cpt_codes = df.get('cptcode')
    cpt_codes = cpt_codes.dropna().astype(str).tolist()

    for count, cpt in enumerate(cpt_codes):
        try:
            formatted = cpt.strip().zfill(5)
            logger.info(f"Processing {count}: {formatted}")
            process_cpt(driver, formatted, count)
        except Exception as e:
            exc_type, _, exc_tb = sys.exc_info()
            logger.error(f"Fatal error at line {exc_tb.tb_lineno}: {e}")

    driver.quit()
    logger.info("Script finished.")

# ------------------------------ Run ------------------------------
if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        exc_type, _, exc_tb = sys.exc_info()
        logger.error(f"Fatal error at line {exc_tb.tb_lineno}: {e}")
