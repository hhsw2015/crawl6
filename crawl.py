from bs4 import BeautifulSoup
from curl_cffi import requests
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import time
from typing import List, Dict
import subprocess

# 设置日志
def setup_logger():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    return logging.getLogger(__name__)

class AVSpider:
    def __init__(self, censored_end_page: int, uncensored_end_page: int, start_page: int = 1, 
                 proxy_url: str = None, use_proxy: bool = False, max_workers: int = 10):
        self.censored_base_url = "https://www.javbus.com/actresses"
        self.uncensored_base_url = "https://www.javbus.com/uncensored/actresses"
        self.censored_end_page = censored_end_page
        self.uncensored_end_page = uncensored_end_page
        self.start_page = start_page
        self.proxy_url = proxy_url if use_proxy else None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        }
        self.proxies = {"http": self.proxy_url, "https": self.proxy_url} if self.proxy_url else {}
        self.logger = setup_logger()
        self.max_workers = max_workers

    def _fetch_url(self, url: str, retries: int = 3) -> str:
        for attempt in range(retries):
            try:
                response = requests.get(
                    url, proxies=self.proxies, headers=self.headers, impersonate="chrome110", timeout=30
                )
                response.raise_for_status()
                self.logger.debug(f"成功获取 {url}，状态码: {response.status_code}")
                return response.text
            except Exception as e:
                self.logger.warning(f"获取 {url} 失败 (尝试 {attempt + 1}/{retries}): {str(e)}")
                time.sleep(2 ** attempt)
        self.logger.error(f"获取 {url} 失败，已达最大重试次数")
        return ""

    def extract_names_from_page(self, page: int, base_url: str) -> List[str]:
        url = f"{base_url}/{page}"
        html_content = self._fetch_url(url)
        if not html_content:
            self.logger.error(f"页面 {page} 未获取到 HTML 内容")
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.find_all("div", class_="item")
        self.logger.info(f"页面 {page} 找到 {len(items)} 个 item 元素")

        names = []
        for item in items:
            span = item.find("div", class_="photo-info").find("span")
            if span:
                name = span.text.strip()
                names.append(name)
                self.logger.debug(f"页面 {page} 提取到名字: {name}")
        return names

    def crawl_pages(self, base_url: str, end_page: int, start_page: int) -> Dict[int, List[str]]:
        results = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_page = {
                executor.submit(self.extract_names_from_page, page, base_url): page
                for page in range(end_page, start_page - 1, -1)
            }
            for future in future_to_page:
                page = future_to_page[future]
                try:
                    names = future.result()
                    results[page] = names
                except Exception as e:
                    self.logger.error(f"页面 {page} 处理失败: {str(e)}")
                    results[page] = []
        return results

    def git_commit(self, filename: str, message: str):
        """提交文件到 Git 仓库"""
        try:
            subprocess.run(["git", "add", filename], check=True)
            result = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
            if result.returncode == 0:
                subprocess.run(["git", "push"], check=True)
                self.logger.info(f"Git commit successful: {message}")
            else:
                self.logger.warning(f"No changes to commit: {result.stderr}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git error: {e.stderr}")
            raise

    def write_and_commit(self, results: Dict[int, List[str]], filename: str):
        """写入文件并每 500 个名字提交一次"""
        total_names = 0
        mode = "a" if os.path.exists(filename) else "w"  # 检查文件是否存在
        with open(filename, mode, encoding="utf-8") as f:
            for page in range(max(results.keys()), min(results.keys()) - 1, -1):
                if page in results and results[page]:
                    f.write(f"\n页面 {page}:\n")
                    for i, name in enumerate(results[page], 1):
                        f.write(f"{i}. {name}\n")
                        total_names += 1
                        if total_names % 500 == 0:
                            f.flush()
                            self.git_commit(filename, f"Update {filename} with {total_names} names")
        if total_names % 500 != 0:
            self.git_commit(filename, f"Update {filename} with {total_names} names (final)")
        self.logger.info(f"总计写入 {total_names} 个名字到 {filename}")

    def crawl_and_save(self):
        self.logger.info("开始爬取有码女优...")
        censored_results = self.crawl_pages(self.censored_base_url, self.censored_end_page, self.start_page)
        self.write_and_commit(censored_results, "censored.txt")

        self.logger.info("开始爬取无码女优...")
        uncensored_results = self.crawl_pages(self.uncensored_base_url, self.uncensored_end_page, self.start_page)
        self.write_and_commit(uncensored_results, "uncensored.txt")

if __name__ == "__main__":
    import os
    censored_end_page = int(os.getenv("CENSORED_END_PAGE", 1019))
    uncensored_end_page = int(os.getenv("UNCENSORED_END_PAGE", 443))
    start_page = int(os.getenv("START_PAGE", 1))

    spider = AVSpider(
        censored_end_page=censored_end_page,
        uncensored_end_page=uncensored_end_page,
        start_page=start_page,
        proxy_url=None,
        use_proxy=False,
        max_workers=10
    )
    spider.crawl_and_save()
