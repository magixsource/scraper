# Scraper for the [17cycling.cn](https://www.17cycling.cn) project.
This scraper project is a 'Web Scraper Chrome Extension' python implementation.

## Highlight
* Support multiple jobs, you can add more jobs in `jobs` directory.
* Support `Web Scraper Chrome Extension` sitemap data.`
  * `SelectorLink`
  * `SelectorText`
  * `SelectorImage`
  * `SelectorPagination`
  * `SelectorElementAttribute`

## Requirements
* Python 3.8+
* Chrome
* Chrome Web Driver
* Selenium
* BeautifulSoup
* requests
* lxml

## Installation
1. Install Python 3.8+
2. Install Chrome Web Driver
3. Execute command `pip install -r requirements.txt` initialize the project


## How to use?
1. Export 'Web  Scraper Chrome Extension' sitemap data as json file.
2. Copy the json file to the project `jobs` directory.
3. Execute `python job_worker.py`
4. After the job is done, you can find the result in `data` directory.

## Roadmap
* [ ] Support schedule job
* [x] Support upload data to database or web API
* [ ] Support data process pipeline
* [x] Support data wash like `regex`、`replace`、`split`
* [x] Support scrape state such like `request_history`
* [x] Support selector `SelectorElementClick` and `SelectorElement`

## Troubleshooting
* Format price: price scraped from website is a string, you can use `regex` to format it. `"[\\$￥]\\d+(?:\\.\\d+)?"` can be used to match price and make sure price string correct.
* Format image  url: image url scraped from website is a string, use `regex` to format it. `"https?://[^\\s>\"\\']+?\\.(?:jpe?g|png|gif|webp)(?:\\?[^\\s>\"\\']*)?"` can be used to make sure url not illegal.