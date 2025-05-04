import concurrent
import json
import traceback

import csv
from io import StringIO

from datetime import datetime

from pathlib import Path

import pydantic

from core.scraping import scrape
from models import ScrapeJob

import asyncio
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
LOG = logging.getLogger(__name__)


async def get_file_job():
    # 从目录jobs中取出所有文件
    folder_path = Path('../jobs')
    files = [f for f in folder_path.iterdir() if f.is_file()]
    jobs = []

    for file in files:
        try:
            file_content = file.read_text(encoding='utf-8')
            job_content = ScrapeJob.model_validate_json(file_content)
            jobs.append(job_content)
        except json.JSONDecodeError as e:
            LOG.error(f"Failed to decode JSON from file {file}: {e}")
        except pydantic.ValidationError as e:
            LOG.error(f"Validation error in file {file}: {e}")

    if not jobs:
        return None
    return jobs


def get_csv_header(dict_obj):
    for middle_dict in dict_obj.values():
        result = ['scrape_uuid', 'scrape_url', 'scrape_at']
        for inner_dict in middle_dict.values():
            for element in inner_dict:
                result.append(element.name)
        return ','.join(result)


def get_csv_data(scraped):
    row_num = 0
    now = datetime.now()
    result = []
    for outer_dict in scraped:
        keys = outer_dict.keys()
        scrape_url = str(list(keys)[0])

        for middle_dict in outer_dict.values():
            row_num += 1
            obj = [str(row_num), scrape_url, now.strftime("%Y-%m-%d %H-%M-%S")]
            for inner_dict in middle_dict.values():
                for element in inner_dict:
                    obj.append(element.text)
            result.append(','.join(str(x) for x in obj))

    quoted_result = '\n'.join('"' + '","'.join(row.split(',')) + '"' for row in result)
    return quoted_result


def get_csv_data_v2(scraped):
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, escapechar='\\')
    # 写入表头
    header = ['scrape_uuid', 'scrape_url', 'scrape_at']
    for middle_dict in scraped[0].values():
        for inner_dict in middle_dict.values():
            for element in inner_dict:
                header.append(element.name)
    writer.writerow(header)

    # 写入数据
    row_num = 0
    now = datetime.now()
    for outer_dict in scraped:
        keys = outer_dict.keys()
        scrape_url = str(list(keys)[0])

        for middle_dict in outer_dict.values():
            row_num += 1
            row = [str(row_num), scrape_url, now.strftime("%Y-%m-%d %H-%M-%S")]
            for inner_dict in middle_dict.values():
                for element in inner_dict:
                    row.append(element.text)
            writer.writerow(row)

    return output.getvalue()


def save_as_csv(job, scraped):
    # 当scraped为空时，不保存文件
    if not scraped or len(scraped) == 0:
        return
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d_%H-%M-%S")
    file_name = job.id + '_' + formatted_time + '.csv'
    file_path = Path(f'../data/{file_name}')
    with file_path.open('w', encoding='utf-8') as f:
        # 写入标题行
        # title_row = get_csv_header(scraped[0])
        # f.write(title_row + '\n')
        # 写入数据行
        # data_rows = get_csv_data(scraped)
        # f.write(data_rows)
        f.write(get_csv_data_v2(scraped))
    LOG.info(f"Saved scraped data to file: {file_path}")


async def process_single_job(job, semaphore):
    async with semaphore:
        LOG.info(f"Beginning processing job: {job}.")
        try:
            scraped = await scrape(job)
            LOG.info(f"Scraped result for url: {job.startUrl}, with result: \n{scraped}")
            save_as_csv(job, scraped)
        except Exception as e:
            LOG.error(f"Exception occurred: {e}\n{traceback.print_exc()}")


async def process_single_job_sync(job):
    try:
        LOG.info(f"Beginning processing job: {job}.")
        scraped = await scrape(job)
        LOG.info(f"Scraped result for url: {job.startUrl}, with result: \n{scraped}")
        save_as_csv(job, scraped)
    except Exception as e:
        LOG.error(f"Exception occurred: {e}\n{traceback.print_exc()}")


async def process_job():
    jobs = await get_file_job()
    if not jobs:
        return
    semaphore = asyncio.Semaphore(3)  # 最大并发数为 3
    tasks = [process_single_job(job, semaphore) for job in jobs]
    await asyncio.gather(*tasks)


async def process_job_sync():
    jobs = await get_file_job()
    if not jobs:
        return

    # 使用 ThreadPoolExecutor 来并发执行
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(pool, lambda j=job: asyncio.run(process_single_job_sync(j)))
            for job in jobs
        ]
        await asyncio.gather(*tasks)


async def main():
    LOG.info("Starting job worker...")
    # while True:
    #     await process_job()
    #     await asyncio.sleep(5)
    await process_job_sync()


if __name__ == "__main__":
    asyncio.run(main())
