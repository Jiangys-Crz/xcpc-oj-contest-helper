"""Offline configuration overview with a strict allowlist for private settings."""
from __future__ import annotations

import platform
import getpass
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

from . import __version__
from .accounts import account_key, api_credentials
from .models import VPError
from .storage import config_directory, read_json, state_directory, workspace_directory


def runtime_user():
    if os.name == 'posix':
        import pwd
        uid = os.geteuid()
        try:
            name = pwd.getpwuid(uid).pw_name
        except KeyError:
            name = '未知用户'
        return name + '（UID ' + str(uid) + '）'
    return getpass.getuser()


def _display(value, default='未设置'):
    if not isinstance(value, (str, int, float)):
        return default
    return ''.join(c for c in str(value) if c.isprintable()).strip() or default


def _load(path, warnings):
    try:
        value = read_json(path)
        if isinstance(value, dict):
            return value
    except (VPError, OSError, ValueError):
        pass
    warnings.append('无法读取配置文件：' + str(path))
    return {}


def print_version_info():
    """No network checks, credential refresh, or files written by this command."""
    warnings = []
    configuration, state = config_directory(), state_directory()
    config = _load(configuration / 'config.json', warnings)
    saved = _load(configuration / 'cloud-account.json', warnings)
    print('XCPC ' + __version__)
    print('运行用户：' + _display(runtime_user()))
    print('系统：' + _display(platform.platform()))
    print('Python：' + platform.python_version())
    print('Python 路径：' + str(Path(sys.executable)))
    try:
        from .updater import installation
        installed = installation()
    except (ImportError, VPError, OSError, ValueError, TypeError):
        installed = {}
    print('安装目录：' + _display(installed.get('install_root'), '源码或非托管安装'))
    machine_wide = installed.get('global') or (installed and not installed.get('portable') and not installed.get('local', True))
    print('安装范围：' + ('整台电脑（所有用户）' if machine_wide else '当前用户 / 自定义安装' if installed else '源码运行'))
    print('命令目录：' + _display(installed.get('bin_directory'), '未记录'))
    print('源码版本：' + _display(installed.get('source_revision'), '未记录'))
    print('当前 xcpc 命令：' + _display(shutil.which('xcpc'), '未在 PATH 中找到'))
    for name in ('git', 'tmux'):
        print(name + ' 路径：' + _display(shutil.which(name), '未安装 / 未在 PATH 中'))
    print('配置目录：' + str(configuration))
    print('状态目录：' + str(state))
    try:
        workspace = str(workspace_directory())
    except (VPError, OSError, ValueError):
        workspace = '配置无效；运行 xcpc path 重新设置'
    print('比赛保存目录：' + workspace)
    print('更新来源：https://vp.cwnupaa.com（服务器已发布更新包）')
    print('源码仓库：https://github.com/Jiangys-Crz/xcpc-oj-contest-helper（xcpc update --github）')

    print('\n云端账号')
    raw_server = saved.get('serverUrl', 'https://vp.cwnupaa.com')
    try:
        parts = urlsplit(raw_server)
        server = raw_server if (parts.scheme in ('https', 'http') and parts.hostname and not
            (parts.username or parts.password or parts.query or parts.fragment or parts.path.strip('/'))) else '配置无效'
    except (ValueError, TypeError):
        server = '配置无效'
    print('服务器：' + _display(server))
    print('本地登录状态：' + ('已保存登录信息' if saved.get('token') else '未登录'))
    print('登录账号：' + _display(saved.get('account')))
    user = saved.get('user') if isinstance(saved.get('user'), dict) else {}
    for key, label in (('useraccount', '账号'), ('username', '昵称'), ('realname', '姓名'),
                       ('numberid', '学号'), ('email', '邮箱')):
        print(label + '：' + _display(user.get(key)))
    print('登录有效期：' + _display(saved.get('expiresAt')))
    print('已保存密码：' + ('是（隐藏）' if saved.get('password') else '否'))
    print('已保存令牌：' + ('是（隐藏）' if saved.get('token') else '否'))

    native = sys.platform in ('darwin', 'win32')
    print('\n原站账号（配置状态，不执行在线登录验证）')
    print('默认登录来源：' + ('Chrome / Edge 扩展' if native else 'Firefox 普通窗口'))
    if native:
        print('浏览器扩展目录：' + str(configuration / 'browser-extension'))
        for browser, label in (('chrome', 'Chrome'), ('edge', 'Edge')):
            print(label + ' 已同步配置数：' + str(len(list((state / 'browser-login' / browser).glob('*.json')))))
    for key, label in (('cf', 'Codeforces'), ('gym', 'Gym'), ('qoj', 'QOJ'),
                       ('cwnuoj', 'CWNUOJ'), ('nowcoder', '牛客')):
        selected = account_key(config, key)
        account = config.get(selected)
        account = account if isinstance(account, dict) else {}
        print(label + '：' + _display(account.get('handle'), '未配置账号'))
        if account.get('uid') is not None:
            print('  用户 ID：' + _display(account['uid']))
        if key in ('cf', 'gym'):
            try:
                complete = bool(api_credentials(account, config, key))
            except (AttributeError, TypeError):
                complete = False
            print('  API key / secret：' + ('已配置（隐藏）' if complete else '未完整配置'))
        print('  Cookie：' + ('已缓存（隐藏）' if account.get('cookie') else '由浏览器读取'))
        if account.get('cookie_source') == 'manual':
            print('  登录来源：手动 Cookie')
        for field, title in (('firefox_profile', 'Firefox 配置目录'), ('browser_profile', '浏览器配置'),
                              ('browser_host', '登录域名')):
            if account.get(field):
                print('  ' + title + '：' + _display(account[field]))
    for warning in warnings:
        print('提示：' + warning)
    print('\n密码、Cookie、API key、API secret 和登录令牌均不显示具体值。')
