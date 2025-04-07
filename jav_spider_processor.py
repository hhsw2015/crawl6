import os
import re
import json
import logging
import sys
import subprocess
from threading import Lock
from scrapy.crawler import CrawlerRunner
from scrapy.utils.project import get_project_settings
from twisted.internet import reactor
from twisted.internet.defer import DeferredList
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

    def update_config(self, name, temp_config_file, category):
        """更新临时 config.ini 文件，根据 category 设置 mosaic 参数"""
        with open(self.config_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(temp_config_file, "w", encoding="utf-8") as f:
            for line in lines:
                if line.strip().startswith("condition ="):
                    f.write(f"condition = {name}\n")
                elif line.strip().startswith("mosaic ="):
                    if category == "censored":
                        f.write("mosaic = yes\n")
                    elif category == "uncensored":
                        f.write("mosaic = no\n")
                    else:
                        f.write("mosaic = all\n")
                else:
                    f.write(line)
        self.logger.info(f"更新临时配置文件 {temp_config_file}: condition={name}, mosaic={'yes' if category == 'censored' else 'no'}")

    def run_javspider(self, temp_config_file, task_id):
        """运行 Scrapy 爬虫并返回 Deferred 对象"""
        settings = get_project_settings()
        settings.set('CONFIG_FILE', temp_config_file, priority='cmdline')
        settings.set('TASK_ID', task_id, priority='cmdline')
        runner = CrawlerRunner(settings)
        self.logger.info(f"启动爬虫任务: task_id={task_id}, config={temp_config_file}")
        deferred = runner.crawl('jav')
        deferred.addCallback(lambda _: self.logger.info(f"爬虫任务完成: task_id={task_id}"))
        return deferred

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
        self.logger.info(f"开始 Git 提交: {files}, 消息: {message}")
        try:
            subprocess.run(["git", "config", "--global", "user.email", "hhsw2015@gmail.com"], check=True)
            subprocess.run(["git", "config", "--global", "user.name", "hhsw2015"], check=True)
            subprocess.run(["git", "add"] + files, check=True)
            result = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
            if result.returncode == 0:
                subprocess.run(["git", "push"], check=True)
                self.logger.info(f"Git 提交成功: {message}")
            else:
                self.logger.warning(f"没有需要提交的更改: {result.stderr}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Git 错误: {e.stderr}")

    def process(self):
        """使用 CrawlerRunner 处理两个文件，每爬完 max_concurrent_tasks 个任务提交一次"""
        censored_lines = self.read_lines(self.censored_file)
        uncensored_lines = self.read_lines(self.uncensored_file)

        os.makedirs("CrawlResult", exist_ok=True)

        tasks = []
        for category, lines in [("censored", censored_lines), ("uncensored", uncensored_lines)]:
            self.logger.info(f"开始处理 {category} 文件，行数: {len(lines)}")
            target_json = self.censored_json if category == "censored" else self.uncensored_json
            target_magnet = self.censored_magnet if category == "censored" else self.uncensored_magnet
            source_file = self.censored_file if category == "censored" else self.uncensored_file
            for line in lines:
                tasks.append((line, category, source_file, target_json, target_magnet))
        
        self.logger.info(f"总任务数: {len(tasks)}")
        max_concurrent_tasks = 5

        def run_task(task):
            line, category, source_file, target_json, target_magnet = task
            name = self.extract_name(line)
            task_id = str(uuid4())[:8]
            temp_config_file = f"config_{task_id}.ini"
            settings = get_project_settings()
            temp_json = f"CrawlResult/{name}_{settings.get('CRAWLRULE', 'default_rule')}_{settings.get('MOSAIC', 'all')}_{task_id}_info.json"
            temp_magnet = f"CrawlResult/{name}_{settings.get('CRAWLRULE', 'default_rule')}_{settings.get('MOSAIC', 'all')}_{task_id}_magnet.txt"

            self.logger.info(f"启动任务: {name} (task_id: {task_id}, category: {category})")
            self.update_config(name, temp_config_file, category)
            deferred = self.run_javspider(temp_config_file, task_id)
            deferred.addCallback(lambda _: self.append_to_summary(temp_json, target_json, temp_magnet, target_magnet, category))
            deferred.addCallback(lambda _: self.comment_line(source_file, line))
            deferred.addCallback(lambda _: os.remove(temp_config_file) if os.path.exists(temp_config_file) else None)
            deferred.addCallback(lambda _: setattr(self, 'crawl_count', self.crawl_count + 1))
            deferred.addCallback(lambda _: self.logger.info(f"任务完成: {name} (task_id: {task_id})"))
            deferred.addErrback(lambda failure: self.logger.error(f"任务失败: {name} (task_id: {task_id}), 错误: {failure}"))
            return deferred

        def process_batch(remaining_tasks, batch_count):
            batch_tasks = remaining_tasks[:max_concurrent_tasks]
            remaining_tasks = remaining_tasks[max_concurrent_tasks:]
            if not batch_tasks:
                self.logger.info("所有批次任务完成，停止 reactor")
                reactor.stop()
                return

            self.logger.info(f"处理第 {batch_count} 批任务，任务数: {len(batch_tasks)}")
            active_deferreds = [run_task(task) for task in batch_tasks]

            def on_batch_complete(result):
                self.logger.info(f"第 {batch_count} 批任务完成，结果: {result}")
                self.git_commit(
                    [self.censored_json, self.uncensored_json, self.censored_magnet, self.uncensored_magnet,
                     self.censored_file, self.uncensored_file],
                    f"Batch {batch_count} update after {self.crawl_count} crawls"
                )
                process_batch(remaining_tasks, batch_count + 1)

            DeferredList(active_deferreds).addBoth(on_batch_complete)

        self.logger.info("启动 Twisted reactor")
        process_batch(tasks, 1)
        reactor.run()

if __name__ == "__main__":
    processor = JavSpiderProcessor(max_threads=5)
    processor.process()
