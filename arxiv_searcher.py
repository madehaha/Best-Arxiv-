import sys
import os
import re
import json
import tempfile
import threading
import urllib.parse
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import PyPDF2
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from openai import OpenAI

# ================= 核心配置 =================
API_KEY = ""  
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
API_MODEL = "glm-5"  # 你说是glm-5模型

# ===========================================

class ArxivSearcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Arxiv高级搜索与论文总结")
        self.root.geometry("800x700")
        
        self.output_file_path = None  # 保存当前运行时的输出文件路径
        self.total_tokens = 0  # 总token使用量
        self.total_prompt_tokens = 0  # 总输入token
        self.total_completion_tokens = 0  # 总输出token
        self.token_records = []  # 记录每篇论文的token使用情况
        self.build_ui()
    
    def build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        search_frame = ttk.LabelFrame(main_frame, text="搜索条件", padding="10")
        search_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(search_frame, text="搜索词条（每行一个: 字段 | 逻辑 | 关键词）").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(search_frame, text="字段可选: all, title, author, abstract, comments, journal_ref").grid(row=1, column=0, sticky=tk.W)
        
        self.terms_text = tk.Text(search_frame, height=5, width=60)
        self.terms_text.grid(row=2, column=0, columnspan=2, pady=5)
        default_text = "all | AND | agent\nall | AND | attack"
        self.terms_text.insert("1.0", default_text)
        
        ttk.Label(search_frame, text="下载篇数:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.num_papers_var = tk.IntVar(value=5)
        ttk.Spinbox(search_frame, from_=1, to=50, textvariable=self.num_papers_var, width=5).grid(row=3, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(search_frame, text="排序方式:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.order_var = tk.StringVar(value="-announced_date_first")
        order_combo = ttk.Combobox(search_frame, textvariable=self.order_var, values=["-announced_date_first", "announced_date_first", "-submitted_date", "submitted_date"])
        order_combo.grid(row=4, column=1, sticky=tk.W, pady=5)
        
        self.delete_pdf_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(search_frame, text="总结后删除PDF", variable=self.delete_pdf_var).grid(row=5, column=0, sticky=tk.W, pady=5)
        
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=10)
        self.search_btn = ttk.Button(btn_frame, text="开始搜索与总结", command=self.start_search)
        self.search_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Token统计显示区域
        token_frame = ttk.LabelFrame(main_frame, text="Token使用统计", padding="5")
        token_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        self.token_label = ttk.Label(token_frame, text="总Token: 0 | 输入: 0 | 输出: 0")
        self.token_label.pack(side=tk.LEFT, padx=5)
        
        output_frame = ttk.LabelFrame(main_frame, text="搜索结果与摘要", padding="10")
        output_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.output_text = scrolledtext.ScrolledText(output_frame, height=18, width=90, wrap=tk.WORD)
        self.output_text.pack(fill=tk.BOTH, expand=True)
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)
        
        self.running = False
    
    def log(self, msg, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.output_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.output_text.see(tk.END)
        self.root.update()
    
    def update_token_display(self):
        """更新界面的Token统计显示"""
        self.token_label.config(
            text=f"总Token: {self.total_tokens} | 输入: {self.total_prompt_tokens} | 输出: {self.total_completion_tokens}"
        )
    
    def parse_terms(self):
        terms = []
        lines = self.terms_text.get("1.0", tk.END).strip().split('\n')
        for line in lines:
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) != 3:
                continue
            field, operator, term = parts
            field_map = {
                'all': 'all',
                'title': 'title',
                'author': 'author',
                'abstract': 'abstract',
                'comments': 'comments',
                'journal': 'journal_ref'
            }
            if field.lower() in field_map:
                field = field_map[field.lower()]
            terms.append({
                "field": field,
                "operator": operator.upper(),
                "term": term
            })
        return terms
    
    def generate_filename(self, terms, max_results, order):
        """生成包含搜索条件和时间戳的文件名"""
        # 提取关键词用于文件名
        keywords = []
        for term in terms:
            # 清理关键词，移除特殊字符
            clean_term = re.sub(r'[^\w\s\u4e00-\u9fff]', '', term['term'])
            clean_term = clean_term.strip()[:20]  # 限制长度
            if clean_term:
                keywords.append(clean_term)
        
        # 用下划线连接关键词
        keyword_str = '_'.join(keywords) if keywords else 'search'
        
        # 限制总长度
        if len(keyword_str) > 50:
            keyword_str = keyword_str[:50]
        
        # 添加篇数信息
        count_info = f"{max_results}papers"
        
        # 添加排序方式缩写
        order_map = {
            "-announced_date_first": "date_desc",
            "announced_date_first": "date_asc",
            "-submitted_date": "submitted_desc",
            "submitted_date": "submitted_asc"
        }
        order_short = order_map.get(order, "default")
        
        # 生成时间戳
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 组合文件名
        filename = f"arxiv_{keyword_str}_{count_info}_{order_short}_{timestamp}.json"
        
        # 替换可能不合法的文件名字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        
        return filename
    
    def build_arxiv_url(self, terms, max_results, order):
        """修复URL构建，移除空参数"""
        params = {
            'advanced': '',
            'classification-physics_archives': 'all',
            'classification-include_cross_list': 'include',
            'date-filter_by': 'all_dates',
            'date-date_type': 'submitted_date',
            'abstracts': 'show',
            'size': str(max_results),
            'order': order,
            'start': '0'
        }
        
        for i, term in enumerate(terms):
            params[f'terms-{i}-operator'] = term['operator']
            params[f'terms-{i}-term'] = term['term']
            params[f'terms-{i}-field'] = term['field']
        
        base_url = "https://arxiv.org/search/advanced"
        query_string = urllib.parse.urlencode(params)
        return f"{base_url}?{query_string}"
    
    def search_and_parse(self, url, max_results):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            self.log(f"请求URL: {url}")
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            papers = []
            results = soup.find_all('li', class_='arxiv-result')
            self.log(f"找到 {len(results)} 个结果")
            
            for result in results[:max_results]:
                title_elem = result.find('p', class_='title')
                title = title_elem.get_text(strip=True) if title_elem else "N/A"
                title = re.sub(r'\s+', ' ', title)
                
                authors_elem = result.find('p', class_='authors')
                authors = authors_elem.get_text(strip=True) if authors_elem else "N/A"
                authors = authors.replace('\n', ',')
                
                comments_elem = result.find('span', class_='comments')
                comments = comments_elem.get_text(strip=True) if comments_elem else ""
                
                pdf_elem = result.find('a', string=re.compile(r'pdf', re.I))
                pdf_url = None
                if pdf_elem:
                    pdf_url = pdf_elem.get('href')
                    if pdf_url.startswith('/'):
                        pdf_url = 'https://arxiv.org' + pdf_url
                
                papers.append({
                    "title": title,
                    "authors": authors,
                    "comments": comments,
                    "pdf_url": pdf_url
                })
            return papers
        except Exception as e:
            self.log(f"搜索或解析出错: {e}", "ERROR")
            return []
    
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
            self.log(f"下载PDF失败 '{paper_title[:50]}...': {e}", "ERROR")
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
        """使用阿里云百炼API调用GLM-5，并返回总结和token使用信息"""
        prompt = f"""帮我阅读这篇论文，要求详细解释，先介绍绪论和背景和故事，然后着重介绍原理和算法要求教会我，包括公式。

论文标题: {paper_title}
论文内容:
{paper_text[:15000]}"""
        
        try:
            client = OpenAI(
                api_key=API_KEY,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

            completion = client.chat.completions.create(
                model=API_MODEL,
                messages=[
                    {"role": "system", "content": "按照用户的要求读取论文并总结，要求不要太繁琐复杂"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=32000
            )
            
            # 提取token使用信息
            usage = completion.usage
            token_info = {
                'prompt_tokens': usage.prompt_tokens,
                'completion_tokens': usage.completion_tokens,
                'total_tokens': usage.total_tokens
            }
            
            # 打印token使用情况
            self.log(f"   Token使用 - 输入: {token_info['prompt_tokens']} | 输出: {token_info['completion_tokens']} | 总计: {token_info['total_tokens']}")
            
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
        """追加写入一篇论文的结果到JSON文件"""
        try:
            # 检查文件是否存在，不存在则创建并写入空列表
            if not os.path.exists(file_path):
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            
            # 读取现有数据
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 追加新结果
            data.append(result)
            
            # 写回文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.log(f"结果已追加保存至: {file_path}")
        except Exception as e:
            self.log(f"保存结果到文件失败: {e}", "ERROR")
    
    def save_token_report(self, file_path):
        """保存Token使用报告到单独的文件"""
        # 生成token报告文件名
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
        
        self.log(f"Token使用报告已保存至: {report_path}")
        
        # 同时保存为CSV格式方便查看
        csv_path = f"{base_name}_token_report.csv"
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write("论文序号,标题,输入Token,输出Token,总Token\n")
            for record in self.token_records:
                # 清理标题中的逗号和换行符
                title_clean = record['title'].replace(',', '，').replace('\n', ' ')
                f.write(f"{record['index']},{title_clean},{record['prompt_tokens']},{record['completion_tokens']},{record['total_tokens']}\n")
            f.write(f"\n总计,,{self.total_prompt_tokens},{self.total_completion_tokens},{self.total_tokens}\n")
        
        self.log(f"Token使用报告(CSV)已保存至: {csv_path}")
    
    def process_paper(self, paper, index, total, output_file):
        title = paper['title']
        comments = paper['comments']
        pdf_url = paper['pdf_url']
        
        self.log(f"[{index}/{total}] 处理: {title[:80]}...")
        
        if not pdf_url:
            self.log(f"没有找到PDF链接，跳过", "WARNING")
            return None
        
        pdf_path = self.download_pdf(pdf_url, title)
        if not pdf_path:
            return None
        
        self.log("   提取PDF文本...")
        paper_text = self.extract_text_from_pdf(pdf_path)
        if not paper_text.strip():
            self.log("   未提取到任何文本，跳过", "WARNING")
            if self.delete_pdf_var.get():
                os.unlink(pdf_path)
            return None
        
        self.log("   调用GLM-5 API进行总结...")
        summary, token_info = self.call_api_summary(title, paper_text, comments)
        
        # 更新token统计
        if token_info:
            self.total_tokens += token_info['total_tokens']
            self.total_prompt_tokens += token_info['prompt_tokens']
            self.total_completion_tokens += token_info['completion_tokens']
            
            # 记录每篇论文的token使用情况
            self.token_records.append({
                'index': index,
                'title': title,
                'prompt_tokens': token_info['prompt_tokens'],
                'completion_tokens': token_info['completion_tokens'],
                'total_tokens': token_info['total_tokens']
            })
            
            # 更新界面显示
            self.update_token_display()
        
        keywords = self.extract_keywords(summary)
        
        if self.delete_pdf_var.get():
            os.unlink(pdf_path)
            self.log("   PDF已删除")
        else:
            self.log(f"   PDF保留在: {pdf_path}")
        
        result = {
            "标题": title,
            "Comments/会议信息": comments,
            "总结": summary,
            "关键词": keywords,
            "token使用": token_info  # 在结果中也记录token信息
        }
        
        self.display_result(result, index)
        
        # 每篇论文处理完后立即追加写入文件
        self.save_result_to_file(result, output_file)
        
        return result
    
    def display_result(self, result, index):
        self.output_text.insert(tk.END, f"\n{'-'*80}\n")
        self.output_text.insert(tk.END, f"论文 {index}: {result['标题']}\n")
        if result['Comments/会议信息']:
            self.output_text.insert(tk.END, f"Comments: {result['Comments/会议信息']}\n")
        self.output_text.insert(tk.END, f"关键词: {', '.join(result['关键词'])}\n")
        
        # 显示token使用信息
        if result.get('token使用'):
            token_info = result['token使用']
            self.output_text.insert(tk.END, f"Token使用 - 输入: {token_info['prompt_tokens']} | 输出: {token_info['completion_tokens']} | 总计: {token_info['total_tokens']}\n")
        
        self.output_text.insert(tk.END, f"\n{result['总结'][:100]}\n")
        self.output_text.insert(tk.END, f"\n{'-'*80}\n")
        self.output_text.see(tk.END)
    
    def start_search(self):
        if self.running:
            return
        self.running = True
        self.search_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        
        # 重置token统计
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.token_records = []
        self.update_token_display()
        
        # 解析搜索条件用于生成文件名
        terms = self.parse_terms()
        max_results = self.num_papers_var.get()
        order = self.order_var.get()
        
        # 生成包含搜索条件和时间戳的文件名
        filename = self.generate_filename(terms, max_results, order)
        self.output_file_path = os.path.join(os.getcwd(), filename)
        
        thread = threading.Thread(target=self.search_and_summarize)
        thread.daemon = True
        thread.start()
    
    def search_and_summarize(self):
        try:
            terms = self.parse_terms()
            if not terms:
                self.log("请输入至少一个搜索词条", "ERROR")
                return
            
            max_results = self.num_papers_var.get()
            order = self.order_var.get()
            
            search_url = self.build_arxiv_url(terms, max_results, order)
            self.log(f"搜索URL: {search_url}")
            
            self.log("正在搜索Arxiv...")
            papers = self.search_and_parse(search_url, max_results)
            if not papers:
                self.log("没有找到论文或解析失败。", "WARNING")
                return
            
            self.log(f"找到 {len(papers)} 篇论文，开始处理...")
            
            # 初始化输出文件为空的JSON数组
            with open(self.output_file_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
            self.log(f"结果将实时保存至: {self.output_file_path}")
            
            for i, paper in enumerate(papers, 1):
                if not self.running:
                    self.log("用户中止操作。")
                    break
                self.process_paper(paper, i, len(papers), self.output_file_path)
            
            # 打印最终token统计
            self.log(f"\n{'='*60}")
            self.log(f"Token使用统计汇总:")
            self.log(f"  总Token: {self.total_tokens}")
            self.log(f"  总输入Token: {self.total_prompt_tokens}")
            self.log(f"  总输出Token: {self.total_completion_tokens}")
            self.log(f"  平均每篇输入: {self.total_prompt_tokens // len(self.token_records) if self.token_records else 0}")
            self.log(f"  平均每篇输出: {self.total_completion_tokens // len(self.token_records) if self.token_records else 0}")
            self.log(f"{'='*60}\n")
            
            self.log(f"\n处理完成。共总结 {i if self.running else i-1} 篇论文。")
            self.log(f"所有结果已保存至: {self.output_file_path}")
            
            # 保存token报告
            if self.token_records:
                self.save_token_report(self.output_file_path)
                
        except Exception as e:
            self.log(f"发生未预期错误: {e}", "ERROR")
        finally:
            self.running = False
            self.search_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
    
    def stop(self):
        self.running = False
        self.log("正在停止...")

if __name__ == "__main__":
    root = tk.Tk()
    app = ArxivSearcherApp(root)
    root.mainloop()
