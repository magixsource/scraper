import asyncio
import itertools
import json
import logging
import os
import random
import re
import threading
import time
from collections import deque
from typing import Any, Optional, List, Dict, Set, Tuple, cast
from urllib.parse import urlparse, urljoin, urlunparse

from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from selenium.common import NoSuchElementException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from seleniumwire import webdriver
from tldextract import tldextract

from core.models import ScrapeJob, ScrapeSelector, CapturedElement
from core.scraping_utils import scrape_content

logging.getLogger('seleniumwire.handler').setLevel(logging.WARNING)
LOG = logging.getLogger(__name__)

# 定义存储请求历史记录的文件路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
REQUEST_HISTORY_FILE = os.path.join(CURRENT_DIR, "request_history.txt")

SCRAPE_LIMIT = -1
SCRAPE_COUNT = 0
ENV = os.getenv("ENV", "dev")


def read_visited_urls(file_path: str) -> Set[str]:
    """从文件中读取已访问的URL，并返回一个集合。"""
    visited_urls = set()
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            for line in file:
                visited_urls.add(line.strip())
    except FileNotFoundError:
        LOG.warning(f"No request history file found at {file_path}")
    return visited_urls


def write_visited_url(file_path: str, url: str):
    """将URL写入文件。"""
    if ENV == "dev":
        return
    LOG.info(f"Writing visited URL: {url}")
    with open(file_path, "a", encoding="utf-8") as file:
        file.write(url + "\n")


def is_same_domain(url: str, original_url: str) -> bool:
    LOG.info(f"Checking if URL is same domain: {url} and {original_url}")

    def extract_main_domain(url_str):
        extracted = tldextract.extract(url_str)
        return f"{extracted.domain}.{extracted.suffix}"

    return extract_main_domain(url) == extract_main_domain(original_url)


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


def handle_click(click_selectors, driver, pages):
    for selector in click_selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, selector.clickElementSelector)
            if element:
                LOG.info(f"Clicking element: {element}")
                element.click()
                time.sleep(selector.delay / 1000)
        except NoSuchElementException:
            LOG.info(f"Element not found: {selector.clickElementSelector}")
    page_source = scrape_content(driver, pages)
    return page_source


def handle_scroll(scroll_selectors, driver, pages):
    initial_elements = len(driver.find_elements(By.CSS_SELECTOR, scroll_selectors[0].selector))
    LOG.info(f"Initial number of elements: {initial_elements}")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        try:
            WebDriverWait(driver, 10).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, scroll_selectors[0].selector)) > initial_elements
            )
            initial_elements = len(driver.find_elements(By.CSS_SELECTOR, scroll_selectors[0].selector))
            LOG.info(f"Number of elements after scroll: {initial_elements}")
        except Exception as e:
            LOG.error(f"Exception occurred: {e}")
            break

    pages.add((driver.page_source, driver.current_url))
    return driver.page_source


# 为每个线程维护一个简单的driver池
class SimpleThreadLocalPool:
    def __init__(self):
        self.local = threading.local()

    def get_pool(self):
        if not hasattr(self.local, 'pool'):
            self.local.pool = deque()
        return self.local.pool

    def get_driver(self, proxies=None):
        pool = self.get_pool()
        if pool:
            return pool.popleft()
        else:
            return create_driver(proxies)

    def release_driver(self, driver):
        pool = self.get_pool()
        if len(pool) < 2:  # 最多保持2个空闲driver
            pool.append(driver)
        else:
            driver.quit()


# 全局线程本地池管理器
driver_pool_manager = SimpleThreadLocalPool()


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
    global SCRAPE_COUNT, SCRAPE_LIMIT
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
        LOG.info(f"URL already visited: {url}")
        return
    if not is_valid_url(url):
        LOG.warning(f"Invalid URL: {url}")
        return
    if not selectors or not is_same_domain(url, original_url) or selectors == []:
        return

    if 0 < SCRAPE_LIMIT <= SCRAPE_COUNT:
        LOG.info(f"==============Reached scrape limit: {SCRAPE_LIMIT}")
        return

    # debug begin 调试：只抓取分页
    # selector_list = [selector for selector in selectors if
    #                  selector.type == 'SelectorPagination' or selector.type == 'SelectorLink']
    # if not selector_list:
    #     return
    # debug end
    driver = None
    try:
        # 从池中获取浏览器实例
        driver = driver_pool_manager.get_driver(proxies)
        driver.implicitly_wait(10)
        # if headers:
        #     driver.request_interceptor = interceptor(headers)
        LOG.info(f"==============Visiting URL: {url}")
        # LOG.info(f"==============selectors: {selectors}")
        driver.get(url)

        final_url = driver.current_url
        visited_urls.add(url)
        visited_urls.add(final_url)

        click_selectors = [selector for selector in selectors if
                           selector.type == 'SelectorElementClick']
        # scroll_selectors = [selector for selector in selectors if
        #                     selector.type == 'SelectorElement' and selector.scroll]
        if click_selectors:
            page_source = handle_click(
                click_selectors,
                driver,
                pages,
            )
        # elif scroll_selectors:
        #     Fixme: 执行的时候报错TimeoutException，暂未解决
        #     page_source = handle_scroll(scroll_selectors, driver, pages)
        else:
            page_source = scrape_content(driver, pages)
    except Exception as e:
        LOG.error(f"Exception occurred: {e}")
        return
    finally:
        # 将浏览器实例返回池中而不是关闭
        if driver:
            driver_pool_manager.release_driver(driver)
    if not multi_page_scrape:
        # 将请求详情页的URL写入文件
        write_visited_url(REQUEST_HISTORY_FILE, final_url)
        SCRAPE_COUNT += 1
        return

    soup = BeautifulSoup(page_source, "html.parser")

    # 收集所有需要爬取的链接
    links_to_crawl = []

    # webscraper logic to scrape multiple pages
    # find all root child selectors by parentSelectors is contains '_root'
    # _selectors = [selector for selector in selectors if '_root' in selector.parentSelectors]
    # if not _selectors:
    _selectors = [selector for selector in selectors if
                  selector.type == 'SelectorPagination' or selector.type == 'SelectorLink']

    # 遍历所有分页选择器
    for _selector in _selectors:
        links = soup.select(_selector.selector)
        for item_link in links:
            link = item_link.get('href')
            # LOG.info(f"===total links {len(links)} ,==========get link : {link}")
            # continue

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
                        LOG.info(f"==============add pagination_urls : {link}")

                    links_to_crawl.append({
                        'url': link,
                        'multi_page_scrape': _multi_page_scrape,
                        'visited_urls': visited_urls,
                        'pages': pages,
                        'pagination_urls': pagination_urls,
                        'original_url': original_url,
                        'proxies': proxies,
                        'selectors': all_child_selectors,
                    })

    # 并发处理所有链接
    if links_to_crawl:
        LOG.info(f"Found {len(links_to_crawl)} links to crawl, processing concurrently")
        tasks = []
        for link_info in links_to_crawl:
            task = make_site_request_with_retry(
                url=link_info['url'],
                multi_page_scrape=link_info['multi_page_scrape'],
                visited_urls=link_info['visited_urls'],
                pages=link_info['pages'],
                pagination_urls=link_info['pagination_urls'],
                original_url=link_info['original_url'],
                proxies=link_info['proxies'],
                selectors=link_info['selectors'],
            )
            tasks.append(task)
        # 并发执行所有任务
        await asyncio.gather(*tasks, return_exceptions=True)


async def make_site_request_with_retry(
        url: str,
        multi_page_scrape: bool = False,
        pagination_urls=None,
        visited_urls=None,
        pages=None,
        original_url: str = "",
        proxies=None,
        selectors=None,
        max_retries: int = 2
) -> None:
    """带重试机制的页面请求函数"""
    LOG.info(f"========make_site_request_with_retry -> {url}")
    for attempt in range(max_retries + 1):
        try:
            await make_site_request(
                url=url,
                multi_page_scrape=multi_page_scrape,
                pagination_urls=pagination_urls,
                visited_urls=visited_urls,
                pages=pages,
                original_url=original_url,
                proxies=proxies,
                selectors=selectors
            )
            return  # 成功则返回
        except Exception as e:
            if attempt < max_retries:
                LOG.warning(f"Attempt {attempt + 1} failed for {url}, retrying... Error: {e}")
                await asyncio.sleep(2 ** attempt)  # 指数退避
            else:
                LOG.error(f"All {max_retries + 1} attempts failed for {url}. Error: {e}")


async def collect_scraped_elements(grouped_list: List[Tuple[str, str]], selectors: List[ScrapeSelector]):
    elements: Dict[str, List[CapturedElement]] = dict()
    key = grouped_list[0][1]

    for page in grouped_list:
        soup = BeautifulSoup(page[0], "html.parser")

        for selector in selectors:
            if selector.type == "SelectorPagination" or selector.type == "SelectorLink":
                continue
            # 对于多个页面，如果已经抓取过并且有值，则跳过
            if len(elements) > 0 and elements.get(selector.id) and elements.get(selector.id, [])[0].text:
                LOG.info(
                    f"======= Skip selector: {selector.id}, and text value is {elements.get(selector.id, [])[0].text}")
                continue
            el = soup.select(selector.selector)
            # LOG.info(f"======= Selector: {selector.selector} and el: {el}")
            if not el:
                LOG.info(f"======= Selector: {selector.selector} not found,set default value.")
                captured_element = CapturedElement(
                    selector=selector.selector, text="", name=selector.id
                )
                elements[selector.id] = [captured_element]
                continue
            for e in el:
                text = ""
                if selector.type == "SelectorElementClick":
                    continue
                if selector.type == "SelectorText":
                    text = str(e.text).replace("\n", "").strip()
                elif selector.type == "SelectorImage":
                    text = str(e.get("src", ""))
                elif selector.type == "SelectorElementAttribute":
                    text = str(e.get(selector.extractAttribute, ""))
                elif selector.type == "SelectorHTML":
                    text = str(e.decode_contents())

                if text:
                    # 如果有正则，则提取正则
                    if selector.regex:
                        text = str(re.findall(selector.regex, text)[0]).strip()
                    # 如果有extraReplace，则替换
                    if selector.extraReplace:
                        text = str(text).replace(selector.extraReplace, "").strip()
                    # 如果有extraPrepend，则添加
                    if selector.extraPrepend:
                        text = str(selector.extraPrepend + text).strip()

                # print(f"==========={e} --> ====={selector.id} : {text}")
                captured_element = CapturedElement(
                    selector=selector.selector, text=text, name=selector.id
                )
                if selector.multiple:
                    if elements.get(selector.id):
                        elements[selector.id].append(captured_element)
                        continue
                if not elements.get(selector.id) or not elements.get(selector.id, [])[0].text:
                    elements[selector.id] = [captured_element]
                else:
                    break

    return {key: elements}


def is_multi_page_scrape(selectors: List[ScrapeSelector]):
    if selectors:
        for selector in selectors:
            if selector.paginationType or selector.type == "SelectorLink":
                return True
    return False


def write_pages(pages_file: str, pages: Set[Tuple[str, str]]) -> None:
    with open(pages_file, "w") as f:
        # 将 Tuple 转为 List，并排序以保证一致性（可选）
        json.dump([list(page) for page in pages], f, ensure_ascii=False, indent=2)


def read_pages(pages_file: str) -> Set[Tuple[str, str]]:
    if not os.path.exists(pages_file):
        return set()

    with open(pages_file, "r") as f:
        pages_data = json.load(f)
        # 确保每个元素都是列表，并转换为 tuple
        return set(tuple(item) if isinstance(item, list) else item for item in pages_data)


def pad_www(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if '.' in netloc and not netloc.startswith("www."):
        # 只有在主域名上才加 www.
        parts = netloc.split('.', 1)
        if len(parts[0]) > 3:  # 简单判断第一个部分不是子域（如 mail.example.com）
            netloc = f"www.{netloc}"
    new_parsed = parsed._replace(netloc=netloc)
    return urlunparse(new_parsed)


async def scrape(job: ScrapeJob):
    visited_urls: Set[str] = read_visited_urls(REQUEST_HISTORY_FILE)
    pages: Set[Tuple[str, str]] = set()
    pagination_urls: Set[str] = set()
    url = job.startUrl[0]
    multi_page_scrape = is_multi_page_scrape(job.selectors)
    selectors = job.selectors if job.selectors else []
    proxies = []

    if multi_page_scrape:
        pagination_urls = set(job.startUrl)

    _ = await make_site_request_with_retry(
        url,
        multi_page_scrape=multi_page_scrape,
        visited_urls=visited_urls,
        pages=pages,
        pagination_urls=pagination_urls,
        original_url=url,
        proxies=proxies,
        selectors=selectors,
    )

    # write_pages(pages_file, pages)

    elements: List[Dict[str, Dict[str, List[CapturedElement]]]] = list()

    print(f"==========pages size : {len(pages)}")

    # 先排序，确保 group by 能按 key 正确分组
    sorted_pages = sorted(pages, key=lambda x: x[1])
    # 将pages按page[1]进行分组
    grouped_pages = itertools.groupby(sorted_pages, key=lambda x: x[1])
    normalized_pagination_set = {pad_www(url) for url in pagination_urls}
    for key, group in grouped_pages:
        # 如果是列表页，则跳过
        if pad_www(key) in normalized_pagination_set:
            continue
        # 注意：需要把 group 转成 list 才能多次遍历
        grouped_list = [(p[0], p[1]) for p in group]
        elements.append(await collect_scraped_elements(grouped_list, selectors))
    return elements
