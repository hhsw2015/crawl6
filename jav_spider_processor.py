import os
import re
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from uuid import uuid4

# 设置日志（带线程名）
def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

class JavSpiderProcessor:
    def __init__(self, censored_file="censored.txt", uncensored_file="uncensored.txt", 
                 config_file="config.ini", max_threads=5):
        self.censored_file = censored_file
        self.uncensored_file = uncensored_file
        self.config_file = config_file
        self.logger = setup_logger()
        self.crawl_count = 0
        self.censored_json = "censored_summary.json"
        self.uncensored_json = "uncensored_summary.json"
        self.censored_magnet = "censored_magnet_summary.txt"
        self.uncensored_magnet = "uncensored_magnet_summary.txt"
        self.max_threads = max_threads
        self.lock = Lock()

        # 设置 Scrapy 项目路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.append(os.path.join(current_dir, 'JavSpider'))
        os.environ['SCRAPY_SETTINGS_MODULE'] = 'JavSpider.settings'

    def read_lines(self, filename):
        """读取文件每一行，只返回未注释的行"""
        with open(filename, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    def comment_line(self, filename, processed_line):
        """将指定行注释掉（线程安全）"""
        with self.lock:
            with open(filename, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(filename, "w", encoding="utf-8") as f:
                for line in lines:
                    stripped_line = line.strip()
                    if stripped_line == processed_line and not stripped_line.startswith("#"):
                        f.write(f"# {line}")
                        self.logger.info(f"已注释 {filename} 中的行: {processed_line}")
                    else:
                        f.write(line)

    def extract_name(self, line):
        """提取括号前的名字"""
        match = re.match(r"([^（]+)(?:（.*）)?", line)
        return match.group(1) if match else line

    def update_config(self, name, temp_config_file):
        """更新临时 config.ini 文件"""
        with open(self.config_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(temp_config_file, "w", encoding="utf-8") as f:
            for line in lines:
                if line.strip().startswith("condition ="):
                    f.write(f"condition = {name}\n")
                else:
                    f.write(line)
        self.logger.info(f"更新临时配置文件 {temp_config_file} 的 condition 为: {name}")

    def run_javspider(self, temp_config_file, task_id):
        """运行 Scrapy 爬虫"""
        settings = get_project_settings()  # 每个线程独立 settings
        settings.set('CONFIG_FILE', temp_config_file, priority='cmdline')
        settings.set('TASK_ID', task_id, priority='cmdline')
        process = CrawlerProcess(settings)
        process.crawl('jav')
        process.start()
        self.logger.info(f"Scrapy 爬虫执行完成 (task_id: {task_id})")

    def append_to_summary(self, source_json, target_json, source_magnet, target_magnet, category):
        """将 JSON 和 magnet 结果追加到汇总文件（线程安全）"""
        with self.lock:
            if not os.path.exists(source_json):
                self.logger.warning(f"未找到 {source_json}")
                return
            if not os.path.exists(target_json):
                with open(target_json, "w", encoding="utf-8") as f:
                    json.dump([], f)
            with open(source_json, "r", encoding="utf-8") as sf:
                source_data = [json.loads(line.strip()) for line in sf if line.strip()]
            with open(target_json, "r", encoding="utf-8") as tf:
                target_data = json.load(tf)
            target_data.extend(source_data)
            with open(target_json, "w", encoding="utf-8") as tf:
                json.dump(target_data, tf, ensure_ascii=False, indent=2)
            self.logger.info(f"已将 {source_json} 追加到 {target_json} ({category})")

            if not os.path.exists(source_magnet):
                self.logger.warning(f"未找到 {source_magnet}")
                return
            if not os.path.exists(target_magnet):
                with open(target_magnet, "w", encoding="utf-8") as f:
                    pass
            with open(source_magnet, "r", encoding="utf-8") as smf:
                magnet_lines = smf.readlines()
            with open(target_magnet, "a", encoding="utf-8") as tmf:
                tmf.writelines(magnet_lines)
            self.logger.info(f"已将 {source_magnet} 追加到 {target_magnet} ({category})")

    def git_commit(self, files, message):
        """提交文件到 Git"""
        self.logger.info(f"尝试提交 {files}，消息: {message}")
        try:
            subprocess.run(["git", "add"] + files, check=True)
            result = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
            if result.returncode == 0:
                subprocess.run(["git", "push"], check=True)
                self.logger.info(f"Git commit successful: {message}")
            else:
                self.logger.warning(f"No changes to commit: {result.stderr}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git error: {e.stderr}")

    def process_task(self, line, category, source_file, target_json, target_magnet):
        """单个任务的处理逻辑"""
        try:
            name = self.extract_name(line)
            task_id = str(uuid4())[:8]
            temp_config_file = f"config_{task_id}.ini"

            # 预测输出文件名
            settings = get_project_settings()
            crawlrule = settings.get('CRAWLRULE', 'default_rule')
            mosaic = settings.get('MOSAIC', 'all')
            temp_json = f"CrawlResult/{name}_{crawlrule}_{mosaic}_{task_id}_info.json"
            temp_magnet = f"CrawlResult/{name}_{crawlrule}_{mosaic}_{task_id}_magnet.txt"

            self.update_config(name, temp_config_file)
            self.run_javspider(temp_config_file, task_id)
            self.append_to_summary(temp_json, target_json, temp_magnet, target_magnet, category)
            self.comment_line(source_file, line)

            if os.path.exists(temp_config_file):
                os.remove(temp_config_file)

            with self.lock:
                self.crawl_count += 1
        except Exception as e:
            self.logger.error(f"任务处理失败（{line}）: {e}")

    def process(self):
        """使用线程池处理两个文件并调用 Scrapy 爬虫"""
        censored_lines = self.read_lines(self.censored_file)
        uncensored_lines = self.read_lines(self.uncensored_file)

        os.makedirs("CrawlResult", exist_ok=True)

        tasks = []
        for category, lines in [("censored", censored_lines), ("uncensored", uncensored_lines)]:
            self.logger.info(f"开始处理 {category} 文件")
            target_json = self.censored_json if category == "censored" else self.uncensored_json
            target_magnet = self.censored_magnet if category == "censored" else self.uncensored_magnet
            source_file = self.censored_file if category == "censored" else self.uncensored_file

            for line in lines:
                tasks.append((line, category, source_file, target_json, target_magnet))

        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            future_to_task = {
                executor.submit(self.process_task, *task): task[0] for task in tasks
            }
            for future in as_completed(future_to_task):
                line = future_to_task[future]
                try:
                    future.result()
                except Exception as e:
                    self.logger.error(f"任务 {line} 执行失败: {e}")

        self.git_commit(
            [self.censored_json, self.uncensored_json, self.censored_magnet, self.uncensored_magnet,
             self.censored_file, self.uncensored_file],
            f"Final update after {self.crawl_count} crawls"
        )

if __name__ == "__main__":
    processor = JavSpiderProcessor(max_threads=5)
    processor.process()
