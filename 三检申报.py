import requests
import time
import os
from datetime import datetime

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

DEFAULT_FORM_DATA = {
    'bDGoK97': '江鹏城',
    'V2MPn1Y': [{'text': '正常（小于或等于37.2度）'}],
    'bxQkoV0': '36.5',
    'RVPmxqR': [{'text': '无以上情况'}],
    'ongyAln': [{'text': '否'}],
}

SANJIAN_KEYWORDS = ['晨检', '午检', '晚检']


def load_session_id():
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.txt')
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            session_id = f.read().strip()
            if session_id:
                return session_id
    except FileNotFoundError:
        pass
    return None


class SanJianClient:
    def __init__(self, session_id: str):
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
            resp = self.session.get(url, params={'type': 'mine', 'pageNum': page})
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
        resp = self.session.get(url, params={'eventId': event_id, 'source': 'share'})
        return resp.json()

    def get_child_name(self):
        url = f'{BASE_URL}/roll/rollList'
        resp = self.session.get(url, params={'type': 'mine', 'pageNum': 0})
        data = resp.json()
        records = data.get('rollList', [])
        for r in records:
            cn = r.get('childName', '')
            if cn:
                return cn
        return ''

    def submit_roll(self, event_id, event_hash, child_name):
        url = f'{BASE_URL}/roll/roll/v2'
        params = {'eventHash': event_hash, 'operator': 'USER'}
        payload = {
            'childName': child_name,
            'extra': DEFAULT_FORM_DATA,
            'eventId': event_id,
        }
        headers = {'dupreqtimestamp': self._ts()}
        resp = self.session.post(url, params=params, json=payload, headers=headers)
        return resp.json()

    def discover_today_events(self, submitted):
        today = f'{datetime.now().month}月{datetime.now().day}日'

        if not submitted:
            return []

        today_submitted_eids = []
        for eid, record in submitted.items():
            event = record.get('event', {})
            title = event.get('title', '')
            if today in title and self.is_sanjian(title):
                today_submitted_eids.append(eid)

        if today_submitted_eids:
            min_eid = min(today_submitted_eids)
            max_eid = max(today_submitted_eids)
        else:
            min_eid = max(submitted.keys())
            max_eid = min_eid

        scan_start = min_eid - 20
        scan_end = max_eid + 20

        today_events = []
        seen = set()

        for eid in range(scan_start, scan_end + 1):
            if eid in seen:
                continue
            seen.add(eid)

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

            time.sleep(0.05)

        return today_events

    def run(self):
        print('=' * 50)
        print('三检申报程序')
        print('=' * 50)

        print(f'\n[配置] 从 config.txt 读取 session-id')
        print(f'[配置] session-id: {self.session_id[:20]}...')

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

        print(f'\n{"=" * 50}')
        confirm = input('是否申报待申报的三检？(y/n): ').strip().lower()
        if confirm != 'y':
            print('[取消] 用户取消申报')
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

            resp = self.submit_roll(eid, event_hash, child_name)
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
