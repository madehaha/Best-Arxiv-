# -*- coding: utf-8 -*-
import sys
import os
import re
import json
import tempfile
import urllib.parse
import time
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import PyPDF2
from openai import OpenAI

# ================= 核心配置 =================
API_KEY = ""
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
API_MODEL = "qwen-plus"  # 阿里云兼容模式必须用通义千问模型
# ===========================================

class ArxivSearcherCLI:
    def __init__(self):
        self.output_file_path = None
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.token_records = []
        self.running = True

    def log(self, msg, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")

    def update_token_display(self):
        print(f"总Token: {self.total_tokens} | 输入: {self.total_prompt_tokens} | 输出: {self.total_completion_tokens}")

    def download_pdf(self, pdf_url, paper_title):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(pdf_url, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            temp_pdf = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
            for chunk in resp.iter_content(chunk_size=8192):
                temp_pdf.write(chunk)
            temp_pdf.close()
            return temp_pdf.name
        except Exception as e:
            self.log(f"下载PDF失败: {e}", "ERROR")
            return None

    def extract_text_from_pdf(self, pdf_path):
        try:
            text = ""
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text
        except Exception as e:
            self.log(f"PDF文本提取失败: {e}", "ERROR")
            return ""

    def call_api_summary(self, paper_title, paper_text, comments=""):
        prompt = f"""帮我阅读这篇论文，要求详细解释，先介绍绪论和背景和故事，然后着重介绍原理和算法要求教会我，包括公式。
论文标题: {paper_title}
论文内容: {paper_text[:15000]}"""

        try:
            client = OpenAI(api_key=API_KEY, base_url=API_URL)
            completion = client.chat.completions.create(
                model=API_MODEL,
                messages=[{"role": "system", "content": "按照用户的要求读取论文并总结，要求不要太繁琐复杂"},
                          {"role": "user", "content": prompt}],
                max_tokens=32000
            )
            usage = completion.usage
            token_info = {
                'prompt_tokens': usage.prompt_tokens,
                'completion_tokens': usage.completion_tokens,
                'total_tokens': usage.total_tokens
            }
            self.log(f"Token使用 - 输入: {token_info['prompt_tokens']} | 输出: {token_info['completion_tokens']}")
            return completion.choices[0].message.content, token_info
        except Exception as e:
            self.log(f"API调用失败: {e}", "ERROR")
            return f"API调用失败: {str(e)}", None

    def extract_keywords(self, summary_text):
        keywords = []
        lines = summary_text.split('\n')
        for line in lines:
            if '关键词' in line or 'Keywords' in line:
                keywords = [k.strip() for k in line.split(':')[-1].split(';')]
                break
        if not keywords:
            words = re.findall(r'\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\b', summary_text[:500])
            keywords = list(set(words))[:5]
        return keywords

    def save_result_to_file(self, result, file_path):
        try:
            data = []
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            data.append(result)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"保存结果失败: {e}", "ERROR")

    def save_token_report(self, file_path):
        base_name = os.path.splitext(file_path)[0]
        report_path = f"{base_name}_token_report.json"
        report = {
            "search_info": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total_papers": len(self.token_records),
                "total_tokens": self.total_tokens,
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens
            },
            "per_paper_tokens": self.token_records
        }
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    def process_paper(self, paper, index, total, output_file):
        title = paper['title']
        comments = paper['comments']
        pdf_url = paper['pdf_url']
        self.log(f"[{index}/{total}] 处理: {title[:80]}...")
        if not pdf_url:
            self.log("无PDF链接，跳过", "WARNING")
            return None

        pdf_path = self.download_pdf(pdf_url, title)
        if not pdf_path:
            return None

        self.log("提取PDF文本...")
        paper_text = self.extract_text_from_pdf(pdf_path)
        if not paper_text.strip():
            self.log("未提取到文本，跳过", "WARNING")
            os.unlink(pdf_path)
            return None

        self.log("调用API总结...")
        summary, token_info = self.call_api_summary(title, paper_text, comments)

        if token_info:
            self.total_tokens += token_info['total_tokens']
            self.total_prompt_tokens += token_info['prompt_tokens']
            self.total_completion_tokens += token_info['completion_tokens']
            self.token_records.append({
                'index': index,
                'title': title,
                'prompt_tokens': token_info['prompt_tokens'],
                'completion_tokens': token_info['completion_tokens'],
                'total_tokens': token_info['total_tokens']
            })
            self.update_token_display()

        keywords = self.extract_keywords(summary)
        os.unlink(pdf_path)
        self.log("PDF已删除")

        result = {
            "标题": title,
            "Comments": comments,
            "总结": summary,
            "关键词": keywords,
            "token使用": token_info
        }
        self.save_result_to_file(result, output_file)
        return result

    def build_arxiv_url(self, terms, size, order, start):
        params = {
            'advanced': '',
            'classification-physics_archives': 'all',
            'classification-include_cross_list': 'include',
            'date-filter_by': 'all_dates',
            'date-date_type': 'submitted_date',
            'abstracts': 'show',
            'size': str(size),
            'order': order,
            'start': str(start)
        }
        for i, term in enumerate(terms):
            params[f'terms-{i}-operator'] = term['operator']
            params[f'terms-{i}-term'] = term['term']
            params[f'terms-{i}-field'] = term['field']
        base_url = "https://arxiv.org/search/advanced"
        query_string = urllib.parse.urlencode(params)
        return f"{base_url}?{query_string}"

    def search_single_page(self, terms, size, order, start):
        url = self.build_arxiv_url(terms, size, order, start)
        print(url)
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            papers = []
            results = soup.find_all('li', class_='arxiv-result')
            for result in results:
                title_elem = result.find('p', class_='title')
                title = title_elem.get_text(strip=True) if title_elem else "N/A"
                title = re.sub(r'\s+', ' ', title)
                authors_elem = result.find('p', class_='authors')
                authors = authors_elem.get_text(strip=True) if authors_elem else "N/A"
                pdf_elem = result.find('a', string=re.compile(r'pdf', re.I))
                pdf_url = None
                if pdf_elem:
                    pdf_url = pdf_elem.get('href')
                    if pdf_url and pdf_url.startswith('/'):
                        pdf_url = 'https://arxiv.org' + pdf_url
                papers.append({"title": title, "authors": authors, "comments": "", "pdf_url": pdf_url})
            return papers
        except Exception as e:
            self.log(f"搜索出错: {e}", "ERROR")
            return []

    def search_all_pages(self, terms, total_papers, size, order):
        all_papers = []
        pages_needed = (total_papers + size - 1) // size
        self.log(f"需要搜索 {pages_needed} 页")
        for page in range(pages_needed):
            if not self.running:
                break
            start = page * size
            current_size = min(size, total_papers - start)
            self.log(f"搜索第 {page+1}/{pages_needed} 页")
            papers = self.search_single_page(terms, size, order, start)
            if papers:
                all_papers.extend(papers)
                if len(papers) < current_size:
                    break
            time.sleep(1)
        return all_papers[:total_papers]

    def generate_filename(self, terms, total_papers):
        keywords = [re.sub(r'[^\w]', '', t['term'][:20]) for t in terms[:3]]
        keyword_str = '_'.join(keywords) or 'search'
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"arxiv_{keyword_str}_{total_papers}papers_{timestamp}.json"

    def run(self, search_terms, total_papers=10, size=10, order="-announced_date_first"):
        self.log("开始搜索...")
        papers = self.search_all_pages(search_terms, total_papers, size, order)
        if not papers:
            self.log("未找到论文")
            return
        self.log(f"找到 {len(papers)} 篇论文")
        filename = self.generate_filename(search_terms, total_papers)
        self.output_file_path = os.path.join(os.getcwd(), filename)
        with open(self.output_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        self.log(f"结果保存到: {self.output_file_path}")

        for i, paper in enumerate(papers, 1):
            self.process_paper(paper, i, len(papers), self.output_file_path)

        self.log("\n处理完成！")
        self.log(f"总Token: {self.total_tokens}")
        self.save_token_report(self.output_file_path)

# ================== 在这里配置你的搜索关键词 ==================
if __name__ == "__main__":
    app = ArxivSearcherCLI()

    # 搜索配置（直接在这里改关键词！）
    search_conditions = [
        {"field": "all", "operator": "AND", "term": "attack"},  # 关键词1
        {"field": "all", "operator": "AND", "term": "agent"},             # 关键词2
    ]

    # 开始运行
    app.run(search_conditions, size=50, total_papers=1)  # 搜索5篇论文
