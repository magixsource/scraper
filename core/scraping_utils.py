import logging
import time
from typing import cast, Set, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LOG = logging.getLogger(__name__)


def scrape_content(driver: webdriver.Chrome, pages: Set[Tuple[str, str]]):
    _ = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    last_height = cast(str, driver.execute_script("return document.body.scrollHeight"))
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

        time.sleep(3)  # Wait for the page to load
        new_height = cast(
            str, driver.execute_script("return document.body.scrollHeight")
        )
        # LOG.info(f"======== scrolling page : {last_height} -> {new_height}")
        if new_height == last_height:
            break

        last_height = new_height
    LOG.info(f"======== Scraping page  -> {driver.current_url}")
    pages.add((driver.page_source, driver.current_url))
    return driver.page_source
