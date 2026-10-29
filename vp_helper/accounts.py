"""Codeforces API credentials shared by VP, practice and live clients."""
from __future__ import annotations

import getpass
import sys

from .models import VPError
from .storage import config_directory, file_lock, read_json, write_json

API_FIELDS = ("api_key", "api_secret")


def pasted_credential(value: str) -> str:
    value = value.replace('\x1b[200~', '').replace('\x1b[201~', '').strip()
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise VPError('请粘贴完整的单个字段值，不包含字段名或其他内容；原配置未修改。')
    return value


def prompt_api_credentials() -> dict:
    print('支持右键或 Ctrl+Shift+V 粘贴；secret 输入不回显，粘贴后按回车。')
    values = {}
    try:
        values['api_key'] = pasted_credential(input('新的 API key（可粘贴）：'))
        values['api_secret'] = pasted_credential(getpass.getpass('新的 API secret（可粘贴，不回显）：'))
    except EOFError:
        raise VPError('输入已取消，原 API 配置未修改。') from None
    print(f"已读取 key {len(values['api_key'])} 字符、secret {len(values['api_secret'])} 字符。")
    return values


def account_key(config: dict, platform: str) -> str:
    if platform not in ("cf", "gym") or platform in config:
        return platform
    other = "gym" if platform == "cf" else "cf"
    return other if other in config else "gym"


def api_credentials(account: dict, config: dict, platform: str) -> dict:
    """Never mix a key/secret pair or copy credentials across different users."""
    handle = account.get("handle", "").casefold()
    if handle:
        selected = account_key(config, platform)
        for key in (selected, "gym" if selected == "cf" else "cf"):
            saved = config.get(key, {})
            if saved.get("handle", "").casefold() == handle and all(saved.get(k) for k in API_FIELDS):
                return {k: saved[k] for k in API_FIELDS}
    return {k: account[k] for k in API_FIELDS} if all(account.get(k) for k in API_FIELDS) else {}


def refresh_api_credentials(account: dict, platform: str) -> None:
    config = read_json(config_directory() / "config.json")
    account.update(api_credentials(account, config, platform))


def configure_api(platform: str = "cf") -> None:
    if not sys.stdin.isatty():
        raise VPError("请在交互终端运行 xcpc api cf，输入新的 API key 和 secret。")
    path = config_directory() / "config.json"
    config = read_json(path)
    selected = account_key(config, platform)
    original = config.get(selected, {})
    handle = original.get("handle", "") or input("Codeforces handle：").strip()
    if not handle or any(c.isspace() for c in handle):
        raise VPError("Codeforces handle 不能为空或包含空白字符，原配置未修改。")
    print("重新设置 Codeforces API，账号：" + handle)
    values = prompt_api_credentials()
    with file_lock(path.with_suffix(".lock")):
        current = read_json(path)
        account = current.setdefault(selected, {})
        if account_key(current, platform) != selected or account.get("handle", "").casefold() not in ("", handle.casefold()):
            raise VPError("输入期间账号配置发生变化，请重新运行 xcpc api cf。原配置未修改。")
        if not account.get("handle"):
            account["handle"] = handle
        account.update(values)
        for other in ("cf", "gym"):
            saved = current.get(other)
            if saved and saved.get("handle", "").casefold() == handle.casefold():
                saved.update(values)
        write_json(path, current)
    print("API 配置已保存，VP、BT、live 共用；后续 API 查询会使用新密钥。")
