import requests
import time
import os
import re
import json
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

BASE_URL = 'https://jl.zjlong.top'

DEFAULT_HEADERS = {
    'charset': 'utf-8',
    'mp-ver': '1.10.18',
    'sdk-ver': '3.4.10',
    'content-type': 'application/json',
    'accept-encoding': 'gzip,compress,br,deflate',
    'user-agent': (
        'Mozilla/5.0 (Linux; Android 14; 22041216C Build/UP1A.231005.007; wv) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.178 '
        'Mobile Safari/537.36 XWEB/1460093 MMWEBSDK/20240404 MMWEBID/3568 '
        'MicroMessenger/8.0.49.2600(0x28003133) WeChat/arm64 Weixin NetType/WIFI '
        'Language/zh_CN ABI/arm64 MiniProgramEnv/android'
    ),
    'referer': 'https://servicewechat.com/wx15e6af63b62a4de4/939/page-frame.html',
}

SANJIAN_KEYWORDS = ['晨检登记', '午检登记', '晚检登记']

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(CONFIG_DIR, 'config.txt')
USER_DATA_FILE = os.path.join(CONFIG_DIR, 'user_data.json')
CACHE_FILE = os.path.join(CONFIG_DIR, 'cache.json')
PATTERNS_FILE = os.path.join(CONFIG_DIR, 'patterns.json')


def load_session_id():
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            session_id = f.read().strip()
            if session_id:
                return session_id
    except FileNotFoundError:
        pass
    return None


def load_user_data():
    try:
        with open(USER_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_user_data(data):
    with open(USER_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(data):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_patterns():
    try:
        with open(PATTERNS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_patterns(data):
    with open(PATTERNS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sort_dates_cross_year(date_strs):
    """跨年排序：如 10月~12月(去年) < 1月~6月(今年)，返回排序后的列表"""
    parsed = []
    for d in date_strs:
        m, day_part = d.split('月')
        day = int(day_part.replace('日', ''))
        parsed.append((int(m), day, d))
    
    # 检测跨年：有 >6 的月和 <=6 的月，说明 <=6 是次年
    months = [p[0] for p in parsed]
    has_late = any(m >= 7 for m in months)
    has_early = any(m <= 6 for m in months)
    cross_year = has_late and has_early
    
    if cross_year:
        parsed.sort(key=lambda x: (x[0] + 12 if x[0] <= 6 else x[0], x[1]))
    else:
        parsed.sort(key=lambda x: (x[0], x[1]))
    
    return [p[2] for p in parsed]


class SanJianClient:
    def __init__(self, session_id):
        self.session_id = session_id
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.headers['session-id'] = session_id

    def _ts(self):
        return str(int(time.time() * 1000))

    def is_sanjian(self, title):
        return any(kw in title for kw in SANJIAN_KEYWORDS)

    def get_submitted_events(self):
        url = f'{BASE_URL}/roll/rollList'
        submitted = {}
        page = 0
        while True:
            resp = self.session.get(url, params={'type': 'mine', 'pageNum': page}, timeout=10)
            data = resp.json()
            records = data.get('rollList', [])
            if not records:
                break
            for r in records:
                eid = r.get('eventId')
                if eid:
                    submitted[eid] = r
            if not data.get('more', False):
                break
            page += 1
        return submitted

    def get_event_detail(self, event_id):
        url = f'{BASE_URL}/roll/roll/v2'
        resp = self.session.get(url, params={'eventId': event_id, 'source': 'share'}, timeout=10)
        return resp.json()

    def get_child_name(self):
        url = f'{BASE_URL}/roll/rollList'
        resp = self.session.get(url, params={'type': 'mine', 'pageNum': 0}, timeout=10)
        data = resp.json()
        records = data.get('rollList', [])
        for r in records:
            cn = r.get('childName', '')
            if cn:
                return cn
        return ''

    def get_form_fields(self, event_id):
        detail = self.get_event_detail(event_id)
        if detail.get('returnCode') == 'SUCCESS':
            event = detail.get('event', {})
            return event.get('extra', [])
        return []

    def submit_roll(self, event_id, event_hash, child_name, form_data):
        url = f'{BASE_URL}/roll/roll/v2'
        params = {'eventHash': event_hash, 'operator': 'USER'}
        payload = {
            'childName': child_name,
            'extra': form_data,
            'eventId': event_id,
        }
        headers = {'dupreqtimestamp': self._ts()}
        resp = self.session.post(url, params=params, json=payload, headers=headers, timeout=10)
        return resp.json()

    def check_event(self, eid, today, shared):
        try:
            detail = self.get_event_detail(eid)
            if detail.get('returnCode') == 'SUCCESS':
                event = detail.get('event', {})
                title = event.get('title', '')
                if today in title:
                    info = {
                        'eventId': eid,
                        'title': title,
                        'is_sanjian': self.is_sanjian(title),
                        'status': event.get('status', ''),
                        'hash': event.get('hash')
                    }
                    with shared['lock']:
                        shared['results'].append(info)
                    return info
        except:
            pass
        return None

    def _learn_from_submitted(self, submitted):
        """从历史申报中提取三检 eventId 规律：每日增长量、三检间距"""
        patterns = load_patterns()
        records = patterns.get('records', {})
        today = f'{datetime.now().month}月{datetime.now().day}日'
        today_date = datetime.now().strftime('%Y-%m-%d')

        # 从 submitted 里把每个日期的三检 eventId 提取出来
        date_map = {}  # { '6月3日': [eventId, eventId, ...] }
        for eid, record in submitted.items():
            title = record.get('event', {}).get('title', '')
            if self.is_sanjian(title):
                # 从 title 中提取日期，如 "4月15日晨检登记" → "4月15日"
                m = re.match(r'(\d+月\d+日)', title)
                if m:
                    d = m.group(1)
                    if d not in date_map:
                        date_map[d] = []
                    date_map[d].append(eid)

        # 更新 records：每个日期取最小/最大 eventId
        for d, eids in date_map.items():
            if d not in records:
                records[d] = {'min': min(eids), 'max': max(eids), 'count': len(eids)}
            else:
                records[d]['min'] = min(records[d]['min'], min(eids))
                records[d]['max'] = max(records[d]['max'], max(eids))
                records[d]['count'] = max(records[d]['count'], len(eids))

        # 按日期排序（跨年感知），计算日均增长
        sorted_dates = _sort_dates_cross_year(list(records.keys()))
        
        daily_growth_rate = 0
        if len(sorted_dates) >= 2:
            first = records[sorted_dates[0]]
            last = records[sorted_dates[-1]]
            total_growth = last['max'] - first['max']
            num_days = len(sorted_dates) - 1
            if num_days > 0:
                daily_growth_rate = total_growth / num_days

        patterns['records'] = records
        patterns['daily_growth_rate'] = daily_growth_rate

        # 记录最后一天最大 eventId
        if sorted_dates:
            patterns['last_max_eid'] = records[sorted_dates[-1]]['max']
            patterns['last_date'] = sorted_dates[-1]

        save_patterns(patterns)
        return patterns

    def _predict_today_range(self, patterns):
        """根据历史规律预测今日三检的 eventId 范围，返回 (start, end) 或 None"""
        daily_growth = patterns.get('daily_growth_rate', 0)
        last_max = patterns.get('last_max_eid', 0)
        last_date_str = patterns.get('last_date', '')

        if not daily_growth or not last_max:
            return None

        # 计算距离上次过了几天
        today_date = datetime.now()
        try:
            parts = last_date_str.replace('日', '').split('月')
            last_date = datetime(today_date.year, int(parts[0]), int(parts[1]))
            # 跨年处理
            if last_date > today_date:
                last_date = datetime(today_date.year - 1, int(parts[0]), int(parts[1]))
        except:
            return None

        days_diff = (today_date - last_date).days
        if days_diff < 0:
            days_diff = 0

        # 预测：last_max + days * 日均增长
        predicted_center = int(last_max + days_diff * daily_growth)

        # 前后各留缓冲（增长率波动 ±30% + 固定缓冲区）
        buffer = max(300, int(daily_growth * 0.3))
        scan_start = predicted_center - buffer
        scan_end = predicted_center + buffer + 500

        return scan_start, scan_end

    def _record_today_pattern(self, patterns, events, today):
        """把今天找到的三检 eventId 记录到规律库"""
        if not events:
            return
        eids = [e['eventId'] for e in events]
        records = patterns.get('records', {})
        records[today] = {'min': min(eids), 'max': max(eids), 'count': len(eids)}
        patterns['records'] = records
        patterns['last_max_eid'] = max(eids)
        patterns['last_date'] = today

        # 重算日均增长
        sorted_dates = _sort_dates_cross_year(list(records.keys()))
        if len(sorted_dates) >= 2:
            first = records[sorted_dates[0]]
            last = records[sorted_dates[-1]]
            total_growth = last['max'] - first['max']
            num_days = len(sorted_dates) - 1
            if num_days > 0:
                patterns['daily_growth_rate'] = total_growth / num_days

        save_patterns(patterns)

    def discover_today_events(self, submitted):
        today = f'{datetime.now().month}月{datetime.now().day}日'
        
        today_submitted_eids = []
        for eid, record in submitted.items():
            event = record.get('event', {})
            title = event.get('title', '')
            if today in title and self.is_sanjian(title):
                today_submitted_eids.append(eid)

        # === 学习历史规律 ===
        patterns = self._learn_from_submitted(submitted)
        
        if today_submitted_eids:
            # 今天已有申报：小范围精确定位
            min_eid = min(today_submitted_eids)
            max_eid = max(today_submitted_eids)
            scan_start = min_eid - 5
            scan_end = max_eid + 5
            sanjian_events = []
        else:
            max_submitted_eid = max(submitted.keys()) if submitted else 0
            cache = load_cache()
            base_eid = cache.get('last_event_id', max_submitted_eid)
            
            # === 优先尝试规律预测 ===
            predicted = self._predict_today_range(patterns)
            sanjian_events = []
            
            if predicted:
                pred_start, pred_end = predicted
                # 确保预测范围在合理区间内（不低于 base_eid-200）
                pred_start = max(pred_start, base_eid - 200)
                print(f'\n  [规律预测] 基于 {len(patterns.get("records",{}))} 天历史')
                print(f'  [规律预测] 日均增长≈{patterns.get("daily_growth_rate",0):.0f}，预测范围 {pred_start}~{pred_end}')
                
                found = self._parallel_scan(pred_start, pred_end, today, '规律预测扫描')
                sanjian_events = [e for e in found if e.get('is_sanjian')]
                
                if sanjian_events:
                    min_found = min(e['eventId'] for e in sanjian_events)
                    max_found = max(e['eventId'] for e in sanjian_events)
                    scan_start = min_found - 5
                    scan_end = max_found + 5
                    save_cache({'last_event_id': min_found, 'date': today})
                    print(f'\n  [预测命中] {len(sanjian_events)} 个三检活动，省去大范围扫描\n')
                else:
                    print(f'\n  [预测未命中] 回退到全范围扫描\n')
            
            # === 回退：三轮递进大范围扫描 ===
            if not sanjian_events:
                scan_ranges = [
                    (base_eid - 200, base_eid + 3000, '第一轮扫描(+/-3200)'),
                    (base_eid + 3000, base_eid + 10000, '第二轮扫描(+7000)'),
                    (base_eid + 10000, base_eid + 20000, '第三轮扫描(+10000)'),
                ]
                
                for scan_start, scan_end, label in scan_ranges:
                    found = self._parallel_scan(scan_start, scan_end, today, label)
                    sanjian_events = [e for e in found if e.get('is_sanjian')]
                    if sanjian_events:
                        min_found = min(e['eventId'] for e in sanjian_events)
                        max_found = max(e['eventId'] for e in sanjian_events)
                        scan_start = min_found - 5
                        scan_end = max_found + 5
                        save_cache({'last_event_id': min_found, 'date': today})
                        print(f'\n  [找到] {len(sanjian_events)} 个三检活动\n')
                        break
                
                if not sanjian_events:
                    scan_start = max_submitted_eid - 50
                    scan_end = max_submitted_eid + 200
                    print(f'\n  [提示] 未找到三检，可能是今天还没有发布\n')

        # === 精扫最终范围：逐条确认并区分已申报/未申报 ===
        today_events = []
        
        for eid in range(scan_start, scan_end + 1):
            if eid in submitted:
                record = submitted[eid]
                event = record.get('event', {})
                title = event.get('title', '')
                if today in title and self.is_sanjian(title):
                    today_events.append({
                        'eventId': eid,
                        'title': title,
                        'submitted': True,
                        'rollId': record.get('rollId')
                    })
                continue

            try:
                detail = self.get_event_detail(eid)
                if detail.get('returnCode') != 'SUCCESS':
                    continue
                event = detail.get('event', {})
                title = event.get('title', '')
                status = event.get('status', '')
                if today in title and status == 'ENROLLING' and self.is_sanjian(title):
                    today_events.append({
                        'eventId': eid,
                        'title': title,
                        'submitted': False,
                        'eventHash': event.get('hash')
                    })
            except:
                continue

        # === 同标题去重：若同一标题出现多次，只保留未申报的（避免重复申报） ===
        dedup = {}
        for e in today_events:
            title = e['title']
            if title not in dedup:
                dedup[title] = e
            else:
                existing = dedup[title]
                # 优先保留未申报的，两者都未申报则保留先遇到的
                if existing.get('submitted') and not e.get('submitted'):
                    dedup[title] = e
        today_events = list(dedup.values())

        # === 记录今天的三检规律，供下次预测 ===
        if today_events:
            self._record_today_pattern(patterns, today_events, today)

        return today_events

    def _parallel_scan(self, scan_start, scan_end, today, label):
        """多线程并行扫描，返回找到的活动列表"""
        total_steps = (scan_end - scan_start) // 10 + 1
        shared = {'results': [], 'lock': threading.Lock(), 'done_count': 0}
        
        print(f'\n  [⚡ {label}] {scan_start} ~ {scan_end}\n')
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {}
            for eid in range(scan_start, scan_end + 1, 10):
                future = executor.submit(self.check_event, eid, today, shared)
                futures[future] = eid
            
            for future in as_completed(futures):
                result = future.result()
                with shared['lock']:
                    shared['done_count'] += 1
                    done = shared['done_count']
                
                progress = int(done / total_steps * 100)
                bar = '█' * (progress // 4) + '░' * (25 - progress // 4)
                
                with shared['lock']:
                    latest = shared['results'][-1:] if shared['results'] else []
                
                line = f'  [{bar}] {progress}% 进度:{done}/{total_steps} 线程:10'
                if latest:
                    e = latest[0]
                    tag = '★三检' if e['is_sanjian'] else '·其他'
                    line += f'  |  最新: [{e["eventId"]}] {tag} {e["title"][:25]}'
                
                # 先清空整行再写新内容
                sys.stdout.write('\r' + line + ' ' * 20)
                sys.stdout.flush()
        
        print()
        return shared['results']

    def collect_user_input(self, form_fields):
        print('\n' + '=' * 50)
        print('请填写申报信息')
        print('=' * 50)
        print('(直接回车使用默认值，输入 * 跳过)\n')

        user_data = {}
        for field in form_fields:
            field_id = field.get('id')
            field_type = field.get('type')
            title = field.get('title', '')
            required = field.get('required', False)

            if field_type == 'input':
                default = field.get('defaultValue', '')
                hint = f' [{default}]' if default else ''
                value = input(f'{title}{hint}: ').strip()
                if value == '*':
                    continue
                if not value and default:
                    value = default
                user_data[field_id] = value

            elif field_type == 'selector':
                options = field.get('options', [])
                multi = field.get('multiSelection', False)
                
                print(f'{title}:')
                for i, opt in enumerate(options):
                    print(f'  {i+1}. {opt.get("text", "")}')
                
                if multi:
                    choice = input('选择(可多选，用逗号分隔): ').strip()
                    if choice == '*':
                        continue
                    selected = []
                    for c in choice.split(','):
                        try:
                            idx = int(c.strip()) - 1
                            if 0 <= idx < len(options):
                                selected.append(options[idx])
                        except:
                            pass
                    if selected:
                        user_data[field_id] = selected
                else:
                    choice = input('选择(输入序号): ').strip()
                    if choice == '*':
                        continue
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            user_data[field_id] = [options[idx]]
                    except:
                        pass
            
            print()

        return user_data

    def run(self):
        print('=' * 50)
        print('三检申报程序')
        print('=' * 50)

        print(f'\n[配置] session-id: {self.session_id[:20]}...')

        print('\n[查询] 获取学号...')
        child_name = self.get_child_name()
        if not child_name:
            print('[错误] 无法获取学号')
            return
        print(f'[结果] 学号: {child_name}')

        print('\n[查询] 获取历史记录...')
        submitted = self.get_submitted_events()
        print(f'[结果] 共 {len(submitted)} 条记录')

        today = f'{datetime.now().month}月{datetime.now().day}日'
        print(f'\n[检测] 扫描 {today} 三检活动...')
        today_events = self.discover_today_events(submitted)

        submitted_today = [e for e in today_events if e.get('submitted')]
        unsubmitted_today = [e for e in today_events if not e.get('submitted')]

        print(f'\n{"=" * 50}')
        print(f'今日三检活动 ({today})')
        print(f'{"=" * 50}')
        print(f'已发布: {len(today_events)} 个')
        print(f'已申报: {len(submitted_today)} 个')
        print(f'未申报: {len(unsubmitted_today)} 个')

        if submitted_today:
            print(f'\n已申报:')
            for e in submitted_today:
                print(f'  [已申报] {e["title"]}')

        if unsubmitted_today:
            print(f'\n待申报:')
            for e in unsubmitted_today:
                print(f'  [待申报] {e["title"]}')

        if not unsubmitted_today:
            print(f'\n[完成] 今日所有三检已申报，无需操作')
            return

        # 加载用户数据，没有则新建
        user_data = load_user_data()
        
        if user_data:
            print(f'\n[配置] 已加载保存的申报参数')
            print(f'[配置] 姓名: {user_data.get("bDGoK97", "未设置")}')
        else:
            first_event = unsubmitted_today[0]
            print(f'\n[获取] 正在获取表单字段...')
            form_fields = self.get_form_fields(first_event['eventId'])
            
            if form_fields:
                user_data = self.collect_user_input(form_fields)
                save_user_data(user_data)
                print(f'\n[保存] 参数已保存到 user_data.json')
            else:
                print('[错误] 无法获取表单字段')
                return

        print(f'\n[申报] 开始申报...')
        results = []
        for i, event_info in enumerate(unsubmitted_today):
            eid = event_info['eventId']
            title = event_info['title']
            event_hash = event_info.get('eventHash')

            print(f'\n[{i+1}/{len(unsubmitted_today)}] {title}')

            if not event_hash:
                detail = self.get_event_detail(eid)
                event = detail.get('event', {})
                event_hash = event.get('hash')

            if not event_hash:
                print(f'  [失败] 无法获取活动信息')
                results.append({'success': False})
                continue

            resp = self.submit_roll(eid, event_hash, child_name, user_data)
            if resp.get('returnCode') == 'SUCCESS':
                roll_id = resp.get('rollId')
                print(f'  [成功] rollId={roll_id}')
                results.append({'success': True})
            else:
                error_msg = resp.get('errorMsg', '未知错误')
                print(f'  [失败] {error_msg}')
                results.append({'success': False})

            time.sleep(0.5)

        success_count = sum(1 for r in results if r.get('success'))
        print(f'\n{"=" * 50}')
        print(f'[完成] 申报结果: {success_count}/{len(results)} 成功')
        print(f'{"=" * 50}')


if __name__ == '__main__':
    SESSION_ID = load_session_id()
    if not SESSION_ID:
        print('[错误] 未找到 config.txt 或文件为空')
        print('请先运行 提取配置.bat 从抓包数据中提取配置')
    else:
        client = SanJianClient(SESSION_ID)
        client.run()
