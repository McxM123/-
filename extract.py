import json
import os
import sys
from urllib.parse import urlparse


def extract_from_har(har_path):
    """从HAR文件提取关键配置信息"""
    
    with open(har_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    entries = data['log']['entries']
    
    session_id = None
    child_name = None
    user_name = None
    base_url = None
    
    for entry in entries:
        req = entry['request']
        url = req['url']
        parsed = urlparse(url)
        
        # 跳过静态资源
        if any(ext in url for ext in ['.mp4', '.png', '.jpg', '.jpeg', '.gif', '.css', '.js']):
            continue
        
        # 提取 session-id（取首个出现的，避免被后续旧令牌覆盖）
        if session_id is None:
            for header in req.get('headers', []):
                if header['name'].lower() == 'session-id':
                    session_id = header['value']
                    break
        
        # 提取 baseUrl
        if 'jl.zjlong.top' in url:
            base_url = f'{parsed.scheme}://{parsed.netloc}'
        
        # 从响应中提取 childName
        resp_body = entry.get('response', {}).get('content', {}).get('text', '')
        if resp_body:
            try:
                resp_json = json.loads(resp_body)
                if 'childName' in resp_json:
                    child_name = resp_json['childName']
            except:
                pass
        
        # 从请求体中提取用户信息
        req_body = req.get('postData', {}).get('text', '')
        if req_body:
            try:
                req_json = json.loads(req_body)
                if 'extra' in req_json:
                    extra = req_json['extra']
                    if 'bDGoK97' in extra:
                        user_name = extra['bDGoK97']
                    if 'childName' in req_json:
                        child_name = req_json['childName']
            except:
                pass
    
    return {
        'session_id': session_id,
        'child_name': child_name,
        'user_name': user_name,
        'base_url': base_url
    }


def main():
    print('=' * 50)
    print('HAR配置提取工具')
    print('=' * 50)
    
    # 查找HAR文件（支持 .har 和 .txt 格式）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    har_files = [f for f in os.listdir(script_dir) if f.endswith(('.har', '.txt')) and 'config' not in f.lower()]
    
    if not har_files:
        print('\n[错误] 当前目录未找到HAR文件')
        print('请将HAR文件放到以下目录:')
        print(f'  {script_dir}')
        input('\n按任意键退出...')
        return
    
    print(f'\n找到 {len(har_files)} 个HAR文件:')
    for i, f in enumerate(har_files):
        print(f'  {i+1}. {f}')
    
    # 选择文件
    if len(har_files) == 1:
        selected = har_files[0]
        print(f'\n自动选择: {selected}')
    else:
        try:
            choice = int(input(f'\n请选择文件 (1-{len(har_files)}): ')) - 1
            selected = har_files[choice]
        except:
            print('[错误] 无效选择')
            input('\n按任意键退出...')
            return
    
    # 提取配置
    print(f'\n[提取] 正在解析 {selected}...')
    config = extract_from_har(selected)
    
    # 显示结果
    print(f'\n{"=" * 50}')
    print('提取结果:')
    print(f'{"=" * 50}')
    print(f'Session ID: {config["session_id"][:30] if config["session_id"] else "未找到"}...')
    print(f'学号: {config["child_name"] or "未找到"}')
    print(f'姓名: {config["user_name"] or "未找到"}')
    print(f'服务器: {config["base_url"] or "未找到"}')
    
    if not config['session_id']:
        print('\n[错误] 未找到session-id，请确认HAR文件包含API请求')
        input('\n按任意键退出...')
        return
    
    # 保存配置
    config_path = os.path.join(script_dir, 'config.txt')
    with open(config_path, 'w', encoding='utf-8') as f:
        f.write(config['session_id'])
    
    print(f'\n[完成] 配置已保存到 config.txt')
    print(f'Session ID: {config["session_id"][:30]}...')
    
    if config['child_name']:
        print(f'学号: {config["child_name"]}')
    
    if config['user_name']:
        print(f'姓名: {config["user_name"]}')
    
    print(f'\n现在可以运行 三检申报.bat 进行申报')
    input('\n按任意键退出...')


if __name__ == '__main__':
    main()