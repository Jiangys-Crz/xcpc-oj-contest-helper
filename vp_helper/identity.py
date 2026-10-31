"""Display association identities without changing native judge handles."""
from __future__ import annotations

from pathlib import Path

from .models import VPError
from .storage import config_directory, file_lock, read_json, write_json


def participant_identity(participant: dict) -> tuple[str, str]:
    title = participant.get('participantTitle')
    subtitle = participant.get('participantSubtitle')
    if isinstance(title, str) and title:
        return title, subtitle if isinstance(subtitle, str) else ''
    source = participant.get('members')
    members = [member for member in source if isinstance(member, dict)] if isinstance(source, list) else []
    label = participant.get('label') or ''
    if participant.get('type') == 'team':
        subtitle = '、'.join(member.get('realname') or member.get('username') or member.get('useraccount') or ''
                            for member in members)
        suffix = '-' + subtitle
        title = label[:-len(suffix)] if subtitle and label.endswith(suffix) else label
        return title, subtitle
    member = members[0] if members else {}
    subtitle = member.get('realname') or ''
    suffix = '-' + subtitle
    title = label[:-len(suffix)] if subtitle and label.endswith(suffix) else member.get('username') or member.get('useraccount') or label
    return title, subtitle


def persist_display_identity(path: Path, state: dict) -> None:
    context = state.get('cloud') or {}
    values = {key: context[key] for key in ('participantTitle', 'participantSubtitle')
              if isinstance(context.get(key), str)}
    opted = bool(state.get('contest', {}).get('extra', {}).get('room_name'))
    if opted and isinstance(context.get('roomName'), str):
        values['roomName'] = context['roomName']
    if not values:
        return
    with file_lock(path.with_suffix('.save.lock')):
        saved = read_json(path)
        previous = saved.get('cloud') or {}
        if not previous or any(previous.get(key) != context.get(key) for key in
                               ('serverUrl', 'roomId', 'userId', 'participantId')):
            return
        if not saved.get('contest', {}).get('extra', {}).get('room_name'):
            values.pop('roomName', None)
        changed = any(previous.get(key) != value for key, value in values.items())
        if 'roomName' in values and saved['contest']['extra']['room_name'] != values['roomName']:
            saved['contest']['extra']['room_name'] = values['roomName']
            changed = True
        if changed:
            saved['cloud'] = {**previous, **values}
            write_json(path, saved)
        state['cloud'] = saved['cloud']
        if opted and saved.get('contest', {}).get('extra', {}).get('room_name'):
            state['contest']['extra']['room_name'] = saved['contest']['extra']['room_name']


def dashboard_identity(state: dict, path: Path | None = None) -> tuple[str, str]:
    context = state.get('cloud')
    if context:
        return participant_identity(context)
    fallback = state.get('handle') or 'me'
    try:
        account = read_json(config_directory() / 'cloud-account.json')
        if not isinstance(account, dict):
            return fallback, ''
        user = account.get('user') or {}
        if not isinstance(user, dict):
            return fallback, ''
        if path is not None:
            owner = read_json(path.with_suffix('.cloud-owner'))
            if not isinstance(owner, dict) or not owner:
                return fallback, ''
            if (str(owner.get('userId')) != str(user.get('id'))
                    or owner.get('serverUrl') != account.get('serverUrl')):
                return fallback, ''
        if account.get('token') and user.get('id'):
            restored = state.get('cloud_restore')
            if (isinstance(restored, dict)
                    and str(restored.get('userId')) == str(user['id'])
                    and restored.get('serverUrl') == account.get('serverUrl')):
                title, subtitle = participant_identity(restored)
                if title:
                    return title, subtitle
            return user.get('username') or user.get('useraccount') or fallback, user.get('realname') or ''
    except (VPError, OSError, ValueError, TypeError):
        pass
    return fallback, ''
