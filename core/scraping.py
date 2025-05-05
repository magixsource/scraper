import logging
import re
from typing import Any, Optional, List, Dict, Set, Tuple
import random

from bs4 import BeautifulSoup
from seleniumwire import webdriver
from fake_useragent import UserAgent
from selenium.webdriver.chrome.options import Options as ChromeOptions
from urllib.parse import urlparse, urljoin

from core.models import ScrapeJob, ScrapeSelector, CapturedElement
from core.scraping_utils import scrape_content

# logging.getLogger('seleniumwire.handler').setLevel(logging.WARNING)
LOG = logging.getLogger(__name__)


def is_same_domain(url: str, original_url: str) -> bool:
    parsed_url = urlparse(url)
    parsed_original_url = urlparse(original_url)
    return parsed_url.netloc == parsed_original_url.netloc or parsed_url.netloc == ""


def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([
            result.scheme in {"http", "https"},  # 协议校验
            bool(result.netloc)  # 域名/IP校验
        ])
    except ValueError:
        return False


def interceptor(headers: Dict[str, Any]):
    def _interceptor(request: Any):
        for key, val in headers.items():
            if request.headers.get(key):
                del request.headers[key]
            request.headers[key] = val
        if "sec-ch-ua" in request.headers:
            original_value = request.headers["sec-ch-ua"]
            del request.headers["sec-ch-ua"]
            modified_value = original_value.replace("HeadlessChrome", "Chrome")
            request.headers["sec-ch-ua"] = modified_value

    return _interceptor


def create_driver(proxies: Optional[List[str]] = []):
    ua = UserAgent()
    chrome_options = ChromeOptions()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"user-agent={ua.random}")

    sw_options = {}
    if proxies:
        selected_proxy = proxies[random.randint(0, len(proxies) - 1)]
        LOG.info(f"Using proxy: {selected_proxy}")

        sw_options = {
            "proxy": {
                "https": f"https://{selected_proxy}",
                "http": f"http://{selected_proxy}",
            }
        }

    # service = Service(
    #     executable_path=r"C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chromedriver.exe")
    # driver = webdriver.Chrome(
    #     service=service,
    #     options=chrome_options,
    #     seleniumwire_options=sw_options,
    # )
    driver = webdriver.Chrome(
        options=chrome_options,
        seleniumwire_options={
            **sw_options,
            "request_wait_timeout": 10,  # 响应最大等待时间设为 10 秒
        },
    )
    return driver


async def make_site_request(
        url: str,
        multi_page_scrape: bool = False,
        pagination_urls=None,
        visited_urls=None,
        pages=None,
        original_url: str = "",
        proxies=None,
        selectors=None,
) -> None:
    """Make basic `GET` request to site using Selenium."""
    # Check if URL has already been visited
    if selectors is None:
        selectors = []
    if proxies is None:
        proxies = []
    if pages is None:
        pages = set()
    if pagination_urls is None:
        pagination_urls = set()
    if visited_urls is None:
        visited_urls = set()
    if url in visited_urls:
        return
    if not is_valid_url(url):
        LOG.warning(f"Invalid URL: {url}")
        return
    if not selectors or not is_same_domain(url, original_url) or selectors == []:
        return

    # debug begin 调试：只抓取分页
    # selector_list = [selector for selector in selectors if
    #                  selector.type == 'SelectorPagination' or selector.type == 'SelectorLink']
    # if not selector_list:
    #     return
    # debug end
    driver = None
    try:
        driver = create_driver(proxies)
        driver.implicitly_wait(10)
        # if headers:
        #     driver.request_interceptor = interceptor(headers)
        LOG.info(f"==============Visiting URL: {url}")
        # LOG.info(f"==============selectors: {selectors}")
        driver.get(url)

        final_url = driver.current_url
        visited_urls.add(url)
        visited_urls.add(final_url)

        page_source = scrape_content(driver, pages)
    except Exception as e:
        LOG.error(f"Exception occurred: {e}")
        return
    finally:
        driver.quit()
    if not multi_page_scrape:
        return

    soup = BeautifulSoup(page_source, "html.parser")

    # webscraper logic to scrape multiple pages
    # find all root child selectors by parentSelectors is contains '_root'
    # _selectors = [selector for selector in selectors if '_root' in selector.parentSelectors]
    # if not _selectors:
    _selectors = [selector for selector in selectors if
                  selector.type == 'SelectorPagination' or selector.type == 'SelectorLink']

    for _selector in _selectors:
        for item_link in soup.select(_selector.selector):
            link = item_link.get('href')
            # LOG.info(f"==============get link : {link}")

            if link:
                if not urlparse(link).netloc:
                    base_url = "{0.scheme}://{0.netloc}".format(urlparse(final_url))
                    link = urljoin(base_url, link)
                if link not in visited_urls and is_same_domain(link, original_url):
                    #  判断是否是列表页
                    if _selector.paginationType:
                        all_child_selectors = selectors
                    else:
                        all_child_selectors = [selector for selector in selectors if
                                               _selector.id in selector.parentSelectors]

                    _multi_page_scrape = is_multi_page_scrape(all_child_selectors)

                    # 如果是列表页，则添加到pagination_urls中
                    if _selector.paginationType:
                        pagination_urls.add(link)

                    await make_site_request(
                        link,
                        multi_page_scrape=_multi_page_scrape,
                        visited_urls=visited_urls,
                        pages=pages,
                        pagination_urls=pagination_urls,
                        original_url=original_url,
                        selectors=all_child_selectors,
                    )


async def collect_scraped_elements(page: Tuple[str, str], selectors: List[ScrapeSelector]):
    soup = BeautifulSoup(page[0], "html.parser")
    elements: Dict[str, List[CapturedElement]] = dict()

    for selector in selectors:
        if selector.type == "SelectorPagination" or selector.type == "SelectorLink":
            continue
        el = soup.select(selector.selector)

        for e in el:
            text = ""
            if selector.type == "SelectorText":
                text = str(e.text).replace("\n", "").strip()
            elif selector.type == "SelectorImage":
                text = e.get("src", "")
            elif selector.type == "SelectorElementAttribute":
                text = e.get(selector.extractAttribute, "")
            elif selector.type == "SelectorHTML":
                text = str(e)

            # 如果有正则，则提取正则
            if len(text) > 0 and len(selector.regex) > 0:
                text = re.findall(selector.regex, text)

            # print(f"==========={e} --> ====={selector.id} : {text}")
            captured_element = CapturedElement(
                selector=selector.selector, text=text, name=selector.id
            )
            if selector.id in elements and selector.multiple:
                elements[selector.id].append(captured_element)
                continue
            elements[selector.id] = [captured_element]

    return {page[1]: elements}


def is_multi_page_scrape(selectors: List[ScrapeSelector]):
    if selectors:
        for selector in selectors:
            if selector.paginationType or selector.type == "SelectorLink":
                return True
    return False


async def scrape(job: ScrapeJob):
    visited_urls: Set[str] = set()
    pages: Set[Tuple[str, str]] = set()
    pagination_urls: Set[str] = set()
    url = job.startUrl[0]
    multi_page_scrape = is_multi_page_scrape(job.selectors)
    selectors = job.selectors if job.selectors else []
    proxies = []

    # 起始页如果是列表页，则添加到pagination_urls中
    if multi_page_scrape:
        pagination_urls = set(job.startUrl)

    _ = await make_site_request(
        url,
        multi_page_scrape=multi_page_scrape,
        visited_urls=visited_urls,
        pages=pages,
        pagination_urls=pagination_urls,
        original_url=url,
        proxies=proxies,
        selectors=selectors,
    )

    elements: List[Dict[str, Dict[str, List[CapturedElement]]]] = list()

    print(f"==========pages size : {len(pages)}")
    for page in pages:
        # 如果是列表页，则跳过
        if page[1] in pagination_urls:
            continue
        elements.append(await collect_scraped_elements(page, selectors))

    return elements
