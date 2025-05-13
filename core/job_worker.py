import concurrent
import json
import os
import traceback

import csv
from io import StringIO

from datetime import datetime

from pathlib import Path

import pydantic
import requests
from dotenv import load_dotenv

from core.scraping import scrape
from models import ScrapeJob

import asyncio
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
LOG = logging.getLogger(__name__)

env_mode = os.getenv("ENV", "dev")
dotenv_path = os.path.join(os.path.dirname(__file__), '..', 'config', f'.env.{env_mode}')
load_dotenv(dotenv_path)


# 获取当前脚本所在目录的父目录（即项目根目录）
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)  # 手动添加根目录到模块搜索路径


async def get_file_job():
    # 从目录jobs中取出所有文件
    folder_path = Path(f'../{env_mode}')
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


def get_csv_data_v2(scraped):
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, escapechar='\\')
    # 写入表头
    header = ['scrape_uuid', 'link', 'scrape_at']
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
    return str(file_path)


def login_to_service():
    login_api_endpoint = os.getenv("ENDPOINT_LOGIN")
    if not login_api_endpoint:
        LOG.error("ENDPOINT_LOGIN is not set in environment variables.")
        return
    try:
        response = requests.post(
            login_api_endpoint,
            json={"username": os.getenv("AUTH_USERNAME"), "password": os.getenv("AUTH_PASSWORD")},
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            return response.json()["id_token"]
        else:
            LOG.error(f"Failed to login. Status code: {response.status_code}, Response: {response.text}")
            return
    except Exception as e:
        LOG.error(f"Error occurred during login: {e}")


def upload_to_web_api(file_path, job_id, auth_token):
    api_endpoint = os.getenv('ENDPOINT_UPLOAD')

    LOG.info(f"Uploading file to {api_endpoint}")
    if not api_endpoint or not auth_token:
        LOG.error("UPLOAD_ENDPOINT or UPLOAD_TOKEN is not set in environment variables.")
        return
    # 判断文件是否存在
    if not os.path.exists(file_path):
        LOG.error(f"Upload failed ! cause of: file {file_path} does not exist.")
        return
    # 读取文件内容
    with open(file_path, 'rb', encoding='utf-8') as f:
        files = {
            'file': (file_path.split('/')[-1], f, 'text/csv')  # 假设上传的文件是 CSV 格式
        }

        # 如果 API 需要额外的字段，可以添加到 data 或 headers 中
        portal_brand_id = os.getenv(f'IDENTIFIER_{job_id.upper()}')
        data = {
            'portalBrandId': portal_brand_id  # 示例参数，根据实际 API 要求调整
        }

        headers = {
            'Authorization': f'Bearer {auth_token}'
        }

        try:
            response = requests.post(
                api_endpoint,
                files=files,
                data=data,
                headers=headers
            )

            if response.status_code == 200:
                LOG.info("File uploaded successfully.")
            else:
                LOG.error(f"Failed to upload file. Status code: {response.status_code}, Response: {response.text}")
        except Exception as e:
            LOG.error(f"Error occurred during upload: {e}")


async def process_single_job_sync(job, auth_token):
    try:
        LOG.info(f"Beginning processing job: {job}.")
        scraped = await scrape(job)
        LOG.info(f"Scraped result for url: {job.startUrl}, with result: \n{scraped}")
        file_path = save_as_csv(job, scraped)
        LOG.info(f"Saved file to: {file_path}")
        # upload to web api
        LOG.info("Upload to web api")
        upload_to_web_api(file_path, job.id, auth_token)
    except Exception as e:
        LOG.error(f"Exception occurred: {e}\n{traceback.print_exc()}")


async def process_job_sync():
    jobs = await get_file_job()
    if not jobs:
        return

    auth_token = login_to_service()
    # auth_token = None
    # 使用 ThreadPoolExecutor 来并发执行
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(pool, lambda j=job: asyncio.run(process_single_job_sync(j, auth_token)))
            for job in jobs
        ]
        await asyncio.gather(*tasks)


async def main():
    LOG.info("Starting job worker...")
    # while True:
    #     await process_job()
    #     await asyncio.sleep(5)
    await process_job_sync()
    # file_path = "../data/gaint_2025-05-09_20-06-05.csv"
    # auth_token = login_to_service()
    # upload_to_web_api(file_path, "gaint", auth_token)


if __name__ == "__main__":
    asyncio.run(main())
