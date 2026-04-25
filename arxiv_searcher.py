import sys
import os
import re
import json
import tempfile
import threading
import urllib.parse
import time
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
API_MODEL = "glm-5"

# ===========================================

class ArxivSearcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Arxiv高级搜索与论文总结")
        self.root.geometry("900x750")
        
        self.output_file_path = None
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.token_records = []
        self.build_ui()
    
    def build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 搜索条件区域
        search_frame = ttk.LabelFrame(main_frame, text="搜索条件", padding="10")
        search_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # 搜索词条列表（动态添加）
        ttk.Label(search_frame, text="搜索词条（可添加多个，支持AND/OR关系）").grid(row=0, column=0, columnspan=3, sticky=tk.W)
        
        # 创建滚动框架用于搜索词条
        self.terms_container = ttk.Frame(search_frame)
        self.terms_container.grid(row=1, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
        
        self.term_frames = []  # 存储每个词条的框架和变量
        self.add_term_row()  # 添加第一个搜索词条
        
        # 按钮区域
        term_btn_frame = ttk.Frame(search_frame)
        term_btn_frame.grid(row=2, column=0, columnspan=3, pady=5)
        ttk.Button(term_btn_frame, text="+ 添加搜索词条", command=self.add_term_row).pack(side=tk.LEFT, padx=5)
        
        # 第二行：其他设置
        setting_frame = ttk.Frame(search_frame)
        setting_frame.grid(row=3, column=0, columnspan=3, pady=10, sticky=(tk.W, tk.E))
        
        # 每页大小
        ttk.Label(setting_frame, text="每页Size:").pack(side=tk.LEFT, padx=5)
        self.size_var = tk.IntVar(value=25)
        size_combo = ttk.Combobox(setting_frame, textvariable=self.size_var, values=[25, 50, 100, 200], width=8)
        size_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(setting_frame, text="总篇数:").pack(side=tk.LEFT, padx=5)
        self.total_papers_var = tk.IntVar(value=25)
        ttk.Spinbox(setting_frame, from_=1, to=500, textvariable=self.total_papers_var, width=8).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(setting_frame, text="排序:").pack(side=tk.LEFT, padx=5)
        self.order_var = tk.StringVar(value="-announced_date_first")
        order_combo = ttk.Combobox(setting_frame, textvariable=self.order_var, 
                                   values=["-announced_date_first", "announced_date_first", "-submitted_date", "submitted_date"], width=20)
        order_combo.pack(side=tk.LEFT, padx=5)
        
        self.delete_pdf_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(setting_frame, text="总结后删除PDF", variable=self.delete_pdf_var).pack(side=tk.LEFT, padx=10)
        
        # 按钮区域
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
        
        # 结果显示区域
        output_frame = ttk.LabelFrame(main_frame, text="搜索结果与摘要", padding="10")
        output_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.output_text = scrolledtext.ScrolledText(output_frame, height=18, width=90, wrap=tk.WORD)
        self.output_text.pack(fill=tk.BOTH, expand=True)
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)
        
        self.running = False
    
    def add_term_row(self):
        """添加一个新的搜索词条行"""
        row_idx = len(self.term_frames)
        frame = ttk.Frame(self.terms_container)
        frame.pack(fill=tk.X, pady=2)
        
        # 字段选择
        field_var = tk.StringVar(value="all")
        field_combo = ttk.Combobox(frame, textvariable=field_var, 
                                   values=["all", "title", "author", "abstract", "comments", "journal_ref"], 
                                   width=12)
        field_combo.pack(side=tk.LEFT, padx=2)
        
        # 逻辑关系（第一行不需要逻辑关系）
        logic_var = tk.StringVar(value="AND")
        if row_idx > 0:  # 第一行不显示逻辑选择
            logic_combo = ttk.Combobox(frame, textvariable=logic_var, values=["AND", "OR"], width=6)
            logic_combo.pack(side=tk.LEFT, padx=2)
        else:
            logic_var.set("")  # 第一行无逻辑关系
        
        # 关键词输入
        keyword_entry = ttk.Entry(frame, width=40)
        keyword_entry.pack(side=tk.LEFT, padx=2)
        
        # 删除按钮
        if row_idx > 0:  # 第一行不能删除
            del_btn = ttk.Button(frame, text="删除", command=lambda: self.remove_term_row(frame, row_idx))
            del_btn.pack(side=tk.LEFT, padx=2)
        
        self.term_frames.append({
            'frame': frame,
            'field_var': field_var,
            'logic_var': logic_var,
            'keyword_entry': keyword_entry,
            'row_idx': row_idx
        })
    
    def remove_term_row(self, frame, row_idx):
        """删除搜索词条行"""
        for i, item in enumerate(self.term_frames):
            if item['row_idx'] == row_idx:
                item['frame'].destroy()
                self.term_frames.pop(i)
                break
        
        # 重新调整索引
        for i, item in enumerate(self.term_frames):
            item['row_idx'] = i
        
        # 第一个词条隐藏逻辑选择
        if self.term_frames:
            # 重新构建逻辑选择组件
            pass
    
    def parse_terms(self):
        """解析搜索词条"""
        terms = []
        for i, item in enumerate(self.term_frames):
            keyword = item['keyword_entry'].get().strip()
            if not keyword:
                continue
            
            field = item['field_var'].get()
            logic = item['logic_var'].get() if i > 0 else "AND"
            
            terms.append({
                "field": field,
                "operator": logic,
                "term": keyword
            })
        return terms
    
    def search_all_pages(self, terms, total_papers, size, order):
        """分页搜索所有论文"""
        all_papers = []
        start = 0
        
        # 计算需要多少页
        pages_needed = (total_papers + size - 1) // size
        self.log(f"需要搜索 {pages_needed} 页，每页 {size} 篇，总计 {total_papers} 篇")
        
        for page in range(pages_needed):
            if not self.running:
                break
            
            start = page * size
            current_size = min(size, total_papers - start)
            
            self.log(f"正在搜索第 {page+1}/{pages_needed} 页 (start={start}, size={current_size})...")
            
            papers = self.search_single_page(terms, current_size, order, start)
            if papers:
                all_papers.extend(papers)
                self.log(f"第 {page+1} 页找到 {len(papers)} 篇论文")
                
                # 如果这一页返回的论文数少于请求数，说明没有更多了
                if len(papers) < current_size:
                    self.log(f"已无更多论文，实际获得 {len(all_papers)} 篇")
                    break
            else:
                self.log(f"第 {page+1} 页未找到结果")
                break
            
            # 避免请求过快
            time.sleep(1)
        
        # 限制总数
        if len(all_papers) > total_papers:
            all_papers = all_papers[:total_papers]
        
        return all_papers
    
    def search_single_page(self, terms, size, order, start):
        """搜索单页论文"""
        url = self.build_arxiv_url(terms, size, order, start)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
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
                authors = authors.replace('\n', ',')
                
                comments_elem = result.find('span', class_='comments')
                comments = comments_elem.get_text(strip=True) if comments_elem else ""
                
                pdf_elem = result.find('a', string=re.compile(r'pdf', re.I))
                pdf_url = None
                if pdf_elem:
                    pdf_url = pdf_elem.get('href')
                    if pdf_url and pdf_url.startswith('/'):
                        pdf_url = 'https://arxiv.org' + pdf_url
                
                papers.append({
                    "title": title,
                    "authors": authors,
                    "comments": comments,
                    "pdf_url": pdf_url
                })
            
            return papers
            
        except Exception as e:
            self.log(f"搜索页出错 (start={start}): {e}", "ERROR")
            return []
    
    def build_arxiv_url(self, terms, size, order, start):
        """构建Arxiv搜索URL"""
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
    
    def generate_filename(self, terms, total_papers, order):
        """生成文件名"""
        keywords = []
        for term in terms[:3]:  # 最多取3个关键词
            keyword = term['term'][:20]
            if keyword:
                keywords.append(re.sub(r'[^\w\s\u4e00-\u9fff]', '', keyword))
        
        keyword_str = '_'.join(keywords) if keywords else 'search'
        if len(keyword_str) > 50:
            keyword_str = keyword_str[:50]
        
        count_info = f"{total_papers}papers"
        
        order_map = {
            "-announced_date_first": "date_desc",
            "announced_date_first": "date_asc",
            "-submitted_date": "submitted_desc",
            "submitted_date": "submitted_asc"
        }
        order_short = order_map.get(order, "default")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"arxiv_{keyword_str}_{count_info}_{order_short}_{timestamp}.json"
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        
        return filename
    
    def log(self, msg, level="INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.output_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.output_text.see(tk.END)
        self.root.update()
    
    def update_token_display(self):
        self.token_label.config(
            text=f"总Token: {self.total_tokens} | 输入: {self.total_prompt_tokens} | 输出: {self.total_completion_tokens}"
        )
    
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
            
            usage = completion.usage
            token_info = {
                'prompt_tokens': usage.prompt_tokens,
                'completion_tokens': usage.completion_tokens,
                'total_tokens': usage.total_tokens
            }
            
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
        try:
            if not os.path.exists(file_path):
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f)
            
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
        
        self.log(f"Token使用报告已保存至: {report_path}")
        
        csv_path = f"{base_name}_token_report.csv"
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write("论文序号,标题,输入Token,输出Token,总Token\n")
            for record in self.token_records:
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
        
        self.log("   调用API进行总结...")
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
            "token使用": token_info
        }
        
        self.display_result(result, index)
        self.save_result_to_file(result, output_file)
        
        return result
    
    def display_result(self, result, index):
        self.output_text.insert(tk.END, f"\n{'-'*80}\n")
        self.output_text.insert(tk.END, f"论文 {index}: {result['标题']}\n")
        if result['Comments/会议信息']:
            self.output_text.insert(tk.END, f"Comments: {result['Comments/会议信息']}\n")
        self.output_text.insert(tk.END, f"关键词: {', '.join(result['关键词'])}\n")
        
        if result.get('token使用'):
            token_info = result['token使用']
            self.output_text.insert(tk.END, f"Token使用 - 输入: {token_info['prompt_tokens']} | 输出: {token_info['completion_tokens']} | 总计: {token_info['total_tokens']}\n")
        
        # 只显示前500字符避免界面卡顿
        preview = result['总结'][:500] + "..." if len(result['总结']) > 500 else result['总结']
        self.output_text.insert(tk.END, f"\n{preview}\n")
        self.output_text.insert(tk.END, f"\n{'-'*80}\n")
        self.output_text.see(tk.END)
    
    def start_search(self):
        if self.running:
            return
        self.running = True
        self.search_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        
        self.total_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.token_records = []
        self.update_token_display()
        
        terms = self.parse_terms()
        if not terms:
            messagebox.showerror("错误", "请至少输入一个搜索关键词")
            self.running = False
            self.search_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            return
        
        total_papers = self.total_papers_var.get()
        size = self.size_var.get()
        order = self.order_var.get()
        
        filename = self.generate_filename(terms, total_papers, order)
        self.output_file_path = os.path.join(os.getcwd(), filename)
        
        thread = threading.Thread(target=self.search_and_summarize, args=(terms, total_papers, size, order))
        thread.daemon = True
        thread.start()
    
    def search_and_summarize(self, terms, total_papers, size, order):
        try:
            self.log(f"搜索条件: {terms}")
            self.log(f"总篇数: {total_papers}, 每页Size: {size}, 排序: {order}")
            
            # 分页搜索所有论文
            papers = self.search_all_pages(terms, total_papers, size, order)
            
            if not papers:
                self.log("没有找到论文。", "WARNING")
                return
            
            self.log(f"共找到 {len(papers)} 篇论文，开始处理...")
            
            # 初始化输出文件
            with open(self.output_file_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
            self.log(f"结果将实时保存至: {self.output_file_path}")
            
            # 处理每篇论文
            for i, paper in enumerate(papers, 1):
                if not self.running:
                    self.log("用户中止操作。")
                    break
                self.process_paper(paper, i, len(papers), self.output_file_path)
            
            # 打印统计
            self.log(f"\n{'='*60}")
            self.log(f"处理完成。共总结 {i if self.running else i-1} 篇论文。")
            self.log(f"Token使用统计汇总:")
            self.log(f"  总Token: {self.total_tokens}")
            self.log(f"  总输入Token: {self.total_prompt_tokens}")
            self.log(f"  总输出Token: {self.total_completion_tokens}")
            if self.token_records:
                self.log(f"  平均每篇输入: {self.total_prompt_tokens // len(self.token_records)}")
                self.log(f"  平均每篇输出: {self.total_completion_tokens // len(self.token_records)}")
            self.log(f"{'='*60}\n")
            
            self.log(f"所有结果已保存至: {self.output_file_path}")
            
            if self.token_records:
                self.save_token_report(self.output_file_path)
                
        except Exception as e:
            self.log(f"发生未预期错误: {e}", "ERROR")
            import traceback
            self.log(traceback.format_exc(), "ERROR")
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
