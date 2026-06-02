import requests
import time
import os
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

    def discover_today_events(self, submitted):
        today = f'{datetime.now().month}月{datetime.now().day}日'
        
        today_submitted_eids = []
        for eid, record in submitted.items():
            event = record.get('event', {})
            title = event.get('title', '')
            if today in title and self.is_sanjian(title):
                today_submitted_eids.append(eid)
        
        if today_submitted_eids:
            min_eid = min(today_submitted_eids)
            max_eid = max(today_submitted_eids)
            scan_start = min_eid - 5
            scan_end = max_eid + 5
        else:
            max_submitted_eid = max(submitted.keys()) if submitted else 0
            cache = load_cache()
            last_eid = cache.get('last_event_id', max_submitted_eid)
            
            found = self._parallel_scan(last_eid - 100, last_eid + 1000, today, '10线程并发扫描')
            
            sanjian_events = [e for e in found if e.get('is_sanjian')]
            if sanjian_events:
                min_found = min(e['eventId'] for e in sanjian_events)
                max_found = max(e['eventId'] for e in sanjian_events)
                scan_start = min_found - 5
                scan_end = max_found + 5
                save_cache({'last_event_id': min_found, 'date': today})
                print(f'\n  [找到] {len(sanjian_events)} 个三检活动\n')
            elif found:
                max_found = max(e['eventId'] for e in found)
                scan_start = max_found
                scan_end = max_found + 1500
                print(f'\n  [提示] 未找到三检，扩大范围...\n')
                found2 = self._parallel_scan(scan_start, scan_end, today, '扩大范围扫描')
                sanjian_events = [e for e in found2 if e.get('is_sanjian')]
                if sanjian_events:
                    min_found = min(e['eventId'] for e in sanjian_events)
                    max_found = max(e['eventId'] for e in sanjian_events)
                    scan_start = min_found - 5
                    scan_end = max_found + 5
                    save_cache({'last_event_id': min_found, 'date': today})
                    print(f'\n  [找到] {len(sanjian_events)} 个三检活动\n')
                else:
                    print(f'\n  [提示] 仍未找到，最后尝试...\n')
                    found3 = self._parallel_scan(max_submitted_eid, max_submitted_eid + 2000, today, '最后尝试扫描')
                    sanjian_events = [e for e in found3 if e.get('is_sanjian')]
                    if sanjian_events:
                        min_found = min(e['eventId'] for e in sanjian_events)
                        max_found = max(e['eventId'] for e in sanjian_events)
                        scan_start = min_found - 5
                        scan_end = max_found + 5
                        save_cache({'last_event_id': min_found, 'date': today})
                        print(f'\n  [找到] {len(sanjian_events)} 个三检活动\n')
                    else:
                        scan_start = max_submitted_eid - 50
                        scan_end = max_submitted_eid + 100
            else:
                scan_start = max_submitted_eid - 50
                scan_end = max_submitted_eid + 100
                print(f'\n  [提示] 未找到任何今天的活动\n')

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

        return today_events

    def _parallel_scan(self, scan_start, scan_end, today, label):
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