import sys
import requests
import re
import concurrent.futures
import traceback
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QObject, pyqtSlot, QThread, pyqtSignal
from PyQt6.QtGui import QClipboard, QIcon

app = QApplication(sys.argv)
app.setWindowIcon(QIcon("logo.ico"))


# ==================== Lims 类（与原来相同） ====================
class Lims:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
        }
        self.session = requests.Session()
        self.logged_in = False

    def get_loginstamp(self):
        url = "http://lmis-shigong.subway.com/maximo/webclient/login/login.jsp"
        r = self.session.get(url, headers=self.headers, verify=False, timeout=10)
        soup = BeautifulSoup(r.text, 'lxml')
        stamp_input = soup.find('input', attrs={"name": "loginstamp"})
        if not stamp_input:
            raise Exception("未找到 loginstamp")
        self.loginstamp = stamp_input['value']

    def get_csrftoken(self, username, password):
        url = "http://lmis-shigong.subway.com/maximo/ui/login"
        data = {
            "allowinsubframe": "null",
            "mobile": "false",
            "login": "jsp",
            "loginstamp": self.loginstamp,
            "username": username,
            "password": password,
        }
        r = self.session.post(url, headers=self.headers, data=data, verify=False, timeout=10)
        soup = BeautifulSoup(r.text, 'lxml')
        csrf_input = soup.find('input', attrs={"id": "csrftokenholder"})
        session_input = soup.find('input', attrs={"id": "uisessionid"})
        if not csrf_input or not session_input:
            raise Exception("登录失败，未找到 csrftoken 或 uisessionid")
        self.csrftoken = csrf_input['value']
        self.uisessionid = session_input['value']
        self.logged_in = True

    def login(self, username="chenmengxi", password="Sbdt1124@"):
        self.get_loginstamp()
        self.get_csrftoken(username, password)

    def maximo(self, number):
        """返回 (name, phone)，若失败返回 ("", "")"""
        if not self.logged_in:
            try:
                self.login()
            except Exception as e:
                print(f"[Lims] 自动登录失败: {e}")
                return "", ""

        try:
            # 第一步：加载应用
            url1 = f"http://lmis-shigong.subway.com/maximo/ui/?event=loadapp&value=udpoint&uisessionid={self.uisessionid}&csrftoken={self.csrftoken}"
            data1 = {
                "event": "loadapp",
                "value": "udpoint",
                "uisessionid": self.uisessionid,
                "csrftoken": self.csrftoken
            }
            self.session.post(url1, headers=self.headers, data=data1, verify=False, timeout=10)

            # 第二步：设置查询条件
            url2 = "http://lmis-shigong.subway.com/maximo/ui/maximo.jsp"
            data2 = {
                "uisessionid": self.uisessionid,
                "csrftoken": self.csrftoken,
                "currentfocus": "mx599",
                "events": '[{"type":"setvalue","targetId":"mx599","value":"' + number + '","requestType":"ASYNC","csrftokenholder":"' + self.csrftoken + '"},{"type":"filterrows","targetId":"mx358","value":"","requestType":"SYNC","csrftokenholder":"' + self.csrftoken + '"}]'
            }
            self.session.post(url2, headers=self.headers, data=data2, verify=False, timeout=10)

            # 第三步：点击查看详情
            data3 = {
                "uisessionid": self.uisessionid,
                "csrftoken": self.csrftoken,
                "currentfocus": "mx841[R:0]",
                "events": '[{"type":"click","targetId":"mx841[R:0]","value":"","requestType":"SYNC","csrftokenholder":"' + self.csrftoken + '"}]'
            }
            r3 = self.session.post(url2, headers=self.headers, data=data3, verify=False, timeout=10)
            soup = BeautifulSoup(r3.text, 'lxml')

            # 提取姓名
            name = ""
            name_candidates = ['mx2135_1', 'mx2135', 'mx2135[1]']
            for candidate in name_candidates:
                td_name = soup.find('td', id=candidate)
                if td_name:
                    input_tag = td_name.find('input')
                    if input_tag:
                        name = input_tag.get('value', '').strip()
                        if name:
                            break

            # 提取电话
            phone = ""
            phone_pattern = r'(?<!\d)1(?:3[0-9]|4[5-9]|5[0-9]|6[2567]|7[0-9]|8[0-9]|9[0-9])\d{8}(?!\d)'
            td_phone = soup.find('table', id='mx2648')
            if td_phone:
                text = td_phone.get_text()
                print(text)
                phones = re.findall(phone_pattern, text)
                if phones:
                    phone = phones[0]

            print(f"[Lims] 编号 {number} => 姓名: {name}, 电话: {phone}")
            return name, phone

        except Exception as e:
            print(f"[Lims] 查询编号 {number} 失败: {e}")
            traceback.print_exc()
            return "", ""


# ==================== 后台工作线程 ====================
class FetchWorker(QThread):
    finished = pyqtSignal(dict)   # {编号: (name, phone)}
    error = pyqtSignal(str)

    def __init__(self, numbers):
        super().__init__()
        self.numbers = numbers

    def fetch_one(self, number):
        try:
            lims = Lims()
            lims.login()
            name, phone = lims.maximo(number)
            return number, (name, phone)
        except Exception as e:
            print(f"[Worker] 线程内异常: {e}")
            traceback.print_exc()
            return number, ("", "")

    def run(self):
        try:
            result = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_to_num = {executor.submit(self.fetch_one, num): num for num in self.numbers}
                for future in concurrent.futures.as_completed(future_to_num):
                    num = future_to_num[future]
                    try:
                        n, (name, phone) = future.result()
                        result[n] = (name, phone)
                    except Exception as e:
                        print(f"[Worker] 处理编号 {num} 时异常: {e}")
                        result[num] = ("", "")
            self.finished.emit(result)
        except Exception as e:
            print(f"[Worker] 线程运行致命错误: {e}")
            traceback.print_exc()
            self.error.emit(str(e))


# ==================== 复制处理器 ====================
class CopyHandler(QObject):
    def __init__(self, clipboard):
        super().__init__()
        self.clipboard = clipboard

    @pyqtSlot(str)
    def copyText(self, text):
        self.clipboard.setText(text)
        print(f"已复制: {text}")


# ==================== 主窗口 ====================
class ChartShowApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.worker = None
        self.is_loading = False
        self.init_ui()
        self.load_data(self.current_date)

    def init_ui(self):
        self.setWindowTitle("Chart Show")
        self.setGeometry(100, 100, 1400, 900)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        top_layout = QHBoxLayout()
        self.date_label = QLabel(f"日期: {self.current_date}")
        self.date_label.setStyleSheet("font-size: 14px; color: #333; padding: 5px;")

        self.today_btn = QPushButton("今天")
        self.today_btn.setCheckable(True)
        self.today_btn.setChecked(True)
        self.today_btn.setFixedSize(80, 32)
        self.today_btn.clicked.connect(self.on_today_clicked)

        self.yesterday_btn = QPushButton("昨天")
        self.yesterday_btn.setCheckable(True)
        self.yesterday_btn.setFixedSize(80, 32)
        self.yesterday_btn.clicked.connect(self.on_yesterday_clicked)

        top_layout.addWidget(self.date_label)
        top_layout.addStretch()
        top_layout.addWidget(self.yesterday_btn)
        top_layout.addWidget(self.today_btn)

        self.web_view = QWebEngineView()
        self.web_view.setMinimumSize(1200, 700)

        settings = self.web_view.page().settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)

        self.channel = QWebChannel(self)
        self.copy_handler = CopyHandler(QApplication.clipboard())
        self.channel.registerObject("copyHandler", self.copy_handler)
        self.web_view.page().setWebChannel(self.channel)

        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.web_view, stretch=1)

    def on_today_clicked(self):
        if self.is_loading:
            return
        if self.today_btn.isChecked():
            self.yesterday_btn.setChecked(False)
            self.current_date = datetime.now().strftime("%Y-%m-%d")
            self.date_label.setText(f"日期: {self.current_date}")
            self.load_data(self.current_date)
        else:
            self.today_btn.setChecked(True)

    def on_yesterday_clicked(self):
        if self.is_loading:
            return
        if self.yesterday_btn.isChecked():
            self.today_btn.setChecked(False)
            yesterday = datetime.now() - timedelta(days=1)
            self.current_date = yesterday.strftime("%Y-%m-%d")
            self.date_label.setText(f"日期: {self.current_date}")
            self.load_data(self.current_date)
        else:
            self.yesterday_btn.setChecked(True)

    # ---------- 辅助函数：清除标签及其后代的所有颜色相关属性 ----------
    def _strip_color_from_tag(self, tag):
        """递归清除标签及其所有后代的 color 样式和 color 属性"""
        if tag.has_attr('style'):
            style = tag['style']
            # 移除 color:...; 或 color:... 等写法
            new_style = re.sub(r'color\s*:\s*[^;]+;?', '', style).strip()
            if new_style:
                tag['style'] = new_style
            else:
                del tag['style']
        if tag.has_attr('color'):
            del tag['color']
        for child in tag.find_all(recursive=False):
            self._strip_color_from_tag(child)

    def _determine_row_color(self, req_type, status):
        """根据请销点类型和审批状态返回颜色名称 ('blue', 'gray', 'black')"""
        req_type = req_type.strip() if req_type else ''
        status = status.strip() if status else ''
        # 规则1: 请点 + 已批准 -> 蓝色
        if req_type == '请点' and status == '已批准':
            return 'blue'
        # 规则2: (请点+等待批准) 或 (销点+已完成/已批准) -> 灰色
        if (req_type == '请点' and status == '等待批准') or \
           (req_type == '销点' and status in ('已完成', '已批准')):
            return 'gray'
        # 其余情况: 黑色
        return 'black'

    def generate_initial_table(self, filtered_rows_cells, header_texts, col_index_map,
                               new_order, time_idx, number_idx, all_numbers, base_url):
        """生成初始HTML表格，施工负责人和电话列包含唯一id，并根据状态设置行文字颜色"""
        soup = BeautifulSoup("<html><body></body></html>", 'html.parser')
        table = soup.new_tag('table')

        # 表头
        thead = soup.new_tag('thead')
        tr_head = soup.new_tag('tr')
        for col_name in new_order:
            th = soup.new_tag('th')
            th.string = col_name
            tr_head.append(th)
        thead.append(tr_head)
        table.append(thead)

        # 获取请销点类型和审批状态的列索引
        req_type_idx = col_index_map.get('请销点类型')
        status_idx = col_index_map.get('审批状态')

        # 获取一个示例“批准作业时间”单元格用于复制样式
        sample_time_cell = None
        for cells in filtered_rows_cells:
            if time_idx is not None and time_idx < len(cells):
                sample_time_cell = cells[time_idx]
                break

        tbody = soup.new_tag('tbody')
        for idx, cells in enumerate(filtered_rows_cells):
            number = all_numbers[idx] if idx < len(all_numbers) else ""
            safe_number = re.sub(r'[^a-zA-Z0-9_]', '_', number)

            # 获取请销点类型和审批状态文本，用于决定行颜色
            req_type_text = ''
            status_text = ''
            if req_type_idx is not None and req_type_idx < len(cells):
                req_type_text = cells[req_type_idx].get_text(strip=True)
            if status_idx is not None and status_idx < len(cells):
                status_text = cells[status_idx].get_text(strip=True)
            row_color = self._determine_row_color(req_type_text, status_text)

            tr = soup.new_tag('tr')
            # 如果不是黑色，则设置行颜色
            if row_color != 'black':
                tr['style'] = f"color: {row_color};"

            for col_name in new_order:
                td = soup.new_tag('td')
                if col_name == '施工负责人':
                    td['id'] = f"leader_{safe_number}"
                    td.string = ""
                    if sample_time_cell:
                        if sample_time_cell.get('class'):
                            td['class'] = sample_time_cell.get('class')
                        if sample_time_cell.get('style'):
                            td['style'] = sample_time_cell.get('style')
                        for attr in ['align', 'bgcolor', 'valign']:
                            if sample_time_cell.get(attr):
                                td[attr] = sample_time_cell[attr]
                elif col_name == '电话':
                    td['id'] = f"phone_{safe_number}"
                    td.string = ""
                    if sample_time_cell:
                        if sample_time_cell.get('class'):
                            td['class'] = sample_time_cell.get('class')
                        if sample_time_cell.get('style'):
                            td['style'] = sample_time_cell.get('style')
                        for attr in ['align', 'bgcolor', 'valign']:
                            if sample_time_cell.get(attr):
                                td[attr] = sample_time_cell[attr]
                else:
                    orig_idx = col_index_map.get(col_name)
                    if orig_idx is not None and orig_idx < len(cells):
                        orig_cell = cells[orig_idx]
                        # 复制原始内容及属性
                        td.extend(list(orig_cell.children))
                        if orig_cell.attrs:
                            for attr, value in orig_cell.attrs.items():
                                td[attr] = value
                        # 特殊处理批准作业时间：截取左侧部分
                        if col_name == '批准作业时间' and orig_idx == time_idx:
                            full_text = td.get_text(strip=True)
                            if '-' in full_text:
                                left_time = full_text.split('-')[0].strip()
                                for descendant in td.descendants:
                                    if isinstance(descendant, str) and descendant.strip():
                                        descendant.replace_with(left_time)
                                        break
                                else:
                                    td.string = left_time
                # 清除当前单元格及其后代中的颜色相关属性，确保行颜色生效
                self._strip_color_from_tag(td)
                tr.append(td)
            tbody.append(tr)

        table.append(tbody)
        soup.body.append(table)

        # 添加样式和复制脚本（与原来相同，并增加高亮更新函数）
        script = f"""
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script>
            new QWebChannel(qt.webChannelTransport, function(channel) {{
                window.copyHandler = channel.objects.copyHandler;
            }});
            function createToast() {{
                var toast = document.createElement('div');
                toast.id = 'copy-toast';
                toast.innerHTML = '<span>✓</span><span>已复制</span>';
                toast.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%) translateY(-100px);background:#667eea;color:white;padding:12px 24px;border-radius:25px;opacity:0;transition:0.3s;z-index:10000;';
                document.body.appendChild(toast);
                return toast;
            }}
            function showToast(text) {{
                var toast = document.getElementById('copy-toast') || createToast();
                toast.children[1].textContent = '已复制: ' + (text.length>20?text.substr(0,20)+'...':text);
                toast.style.opacity = '1';
                toast.style.transform = 'translateX(-50%) translateY(0)';
                setTimeout(() => {{ toast.style.opacity = '0'; toast.style.transform = 'translateX(-50%) translateY(-100px)'; }}, 2000);
            }}
            function updateCell(cellId, newText) {{
                var cell = document.getElementById(cellId);
                if (cell) {{
                    var oldBg = cell.style.backgroundColor;
                    cell.innerText = newText;
                    cell.style.backgroundColor = '#d4edda';
                    setTimeout(() => {{ cell.style.backgroundColor = oldBg; }}, 500);
                }}
            }}
            document.addEventListener('DOMContentLoaded', function() {{
                document.querySelectorAll('td, th').forEach(cell => {{
                    cell.addEventListener('click', function() {{
                        var text = (this.innerText || this.textContent).trim();
                        if(text && window.copyHandler) {{
                            window.copyHandler.copyText(text);
                            showToast(text);
                            var bg = this.style.backgroundColor;
                            this.style.backgroundColor = '#d4edda';
                            setTimeout(() => this.style.backgroundColor = bg, 200);
                        }}
                    }});
                    cell.addEventListener('mouseenter', function(){{ this.style.cursor='pointer'; this.style.boxShadow='inset 0 0 0 2px #667eea'; }});
                    cell.addEventListener('mouseleave', function(){{ this.style.boxShadow='none'; }});
                }});
            }});
        </script>
        <style>
            table {{ border-collapse: collapse; width: 100%; font-family: Arial; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            td:hover {{ background-color: #f5f7fa !important; }}
        </style>
        """
        if soup.head is None:
            head = soup.new_tag('head')
            soup.html.insert(0, head)
        soup.head.append(BeautifulSoup(script, 'html.parser'))
        return str(soup)

    def load_data(self, date_str):
        """加载报表数据并立即显示空白表格（施工负责人/电话列空白）"""
        if self.is_loading:
            print("[Main] 已有加载任务，忽略重复请求")
            return
        self.is_loading = True
        self.today_btn.setEnabled(False)
        self.yesterday_btn.setEnabled(False)

        print(f"[Main] 开始加载日期 {date_str} 的数据")
        try:
            url = "http://lmis-shigong.subway.com/chartshow/report/pointlist/pointlist_q.jsp"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            data = {
                "SITEID": "LINE5",
                "sd": date_str,
                "DEPT": "",
                "WT": "C",
                "PLANTYPE": "ALL",
                "STAT": "",
                "TO": "3000",
                "B1": "提交"
            }
            response = requests.post(url=url, headers=headers, data=data, timeout=15)
            response.encoding = 'utf-8'
            print(f"[Main] 报表请求状态: {response.status_code}")

            if response.status_code != 200:
                self.web_view.setHtml(f"<h3>请求失败，状态码: {response.status_code}</h3>")
                self.is_loading = False
                self.today_btn.setEnabled(True)
                self.yesterday_btn.setEnabled(True)
                return

            soup = BeautifulSoup(response.text, 'html.parser')
            header_div = soup.find('div', class_='header')
            if header_div:
                header_div.decompose()

            table = soup.find('table')
            if not table:
                self.web_view.setHtml("<h3>未找到表格数据</h3>")
                self.is_loading = False
                self.today_btn.setEnabled(True)
                self.yesterday_btn.setEnabled(True)
                return

            rows = table.find_all('tr')
            if len(rows) < 2:
                self.web_view.setHtml("<h3>表格无数据</h3>")
                self.is_loading = False
                self.today_btn.setEnabled(True)
                self.yesterday_btn.setEnabled(True)
                return

            header_cells = rows[0].find_all(['td', 'th'])
            header_texts = [cell.get_text(strip=True) for cell in header_cells]
            col_index_map = {name: idx for idx, name in enumerate(header_texts)}
            print(f"[Main] 表头列: {list(col_index_map.keys())}")

            new_order = ['编号', '作业部门', '作业内容', '批准作业时间',
                         '施工负责人', '电话',
                         '作业代码', '销点时间', '作业区域', '请点车站',
                         '请销点类型', '审批状态']

            station_idx = col_index_map.get('请点车站')
            area_idx = col_index_map.get('作业区域')
            time_idx = col_index_map.get('批准作业时间')
            number_idx = col_index_map.get('编号')

            all_numbers = []
            filtered_rows_cells = []
            for row_idx, row in enumerate(rows[1:], start=1):
                cells = row.find_all(['td', 'th'])
                has_taojin = False
                if station_idx is not None and station_idx < len(cells):
                    if '淘金' in cells[station_idx].get_text(strip=True):
                        has_taojin = True
                if area_idx is not None and area_idx < len(cells):
                    if '淘金' in cells[area_idx].get_text(strip=True):
                        has_taojin = True
                if not has_taojin:
                    continue
                filtered_rows_cells.append(cells)
                if number_idx is not None and number_idx < len(cells):
                    num = cells[number_idx].get_text(strip=True)
                else:
                    num = ""
                all_numbers.append(num)

            print(f"[Main] 过滤后剩余 {len(filtered_rows_cells)} 条记录")

            if not filtered_rows_cells:
                self.web_view.setHtml("<h3>当前日期无相关施工数据</h3>")
                self.is_loading = False
                self.today_btn.setEnabled(True)
                self.yesterday_btn.setEnabled(True)
                return

            initial_html = self.generate_initial_table(
                filtered_rows_cells, header_texts, col_index_map,
                new_order, time_idx, number_idx, all_numbers, url
            )
            self.web_view.setHtml(initial_html, QUrl(url))

            self.pending_numbers = all_numbers
            self.pending_url = url

            self.worker = FetchWorker(all_numbers)
            self.worker.finished.connect(self.on_fetch_finished)
            self.worker.error.connect(self.on_fetch_error)
            self.worker.start()
            print("[Main] 后台线程已启动，表格已显示（空白负责人/电话列）")

        except Exception as e:
            print(f"[Main] load_data 异常: {e}")
            traceback.print_exc()
            self.web_view.setHtml(f"<h3>加载失败: {e}</h3>")
            self.is_loading = False
            self.today_btn.setEnabled(True)
            self.yesterday_btn.setEnabled(True)

    def on_fetch_finished(self, contact_dict):
        print("[Main] 后台数据获取完成，开始更新表格")
        updates = []
        for number, (name, phone) in contact_dict.items():
            safe_number = re.sub(r'[^a-zA-Z0-9_]', '_', number)
            if name:
                updates.append(f"updateCell('leader_{safe_number}', '{name.replace("'", "\\'")}');")
            if phone:
                updates.append(f"updateCell('phone_{safe_number}', '{phone.replace("'", "\\'")}');")
        if updates:
            js_code = "\n".join(updates)
            self.web_view.page().runJavaScript(js_code)
            print(f"[Main] 已更新 {len(updates)} 个单元格")
        else:
            print("[Main] 无数据需要更新")

        self.is_loading = False
        self.today_btn.setEnabled(True)
        self.yesterday_btn.setEnabled(True)

    def on_fetch_error(self, err_msg):
        print(f"[Main] 后台线程错误: {err_msg}")
        self.web_view.setHtml(f"<h3>获取负责人信息失败: {err_msg}</h3>")
        self.is_loading = False
        self.today_btn.setEnabled(True)
        self.yesterday_btn.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = ChartShowApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()