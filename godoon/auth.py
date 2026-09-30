"""Import and verify the user's existing WeChat mini-program session."""
import base64
import getpass
import json
import math
import re
import time

from .api import AuthenticationError, CLUB_ID, Client
from .sessions import read_sessions


def normalize_token(value):
    token = value.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
        raise AuthenticationError('Token 格式不正确，请复制完整的登录凭据。')
    return token


def expiration(token):
    try:
        payload = token.split('.')[1]
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        expires = data['exp']
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise ValueError
        return expires
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise AuthenticationError('无法读取登录有效期，请重新获取 Token。') from error


def verify(token):
    token = normalize_token(token)
    if expiration(token) <= time.time() + 3600:
        raise AuthenticationError('登录态剩余时间不足一小时，请在微信中重新进入企业咕咚。')
    client = Client(token)
    try:
        profile = client.request('GET', '/v1/user/user_info', {})
        if profile['last_club'] is None or profile['last_club']['id'] != CLUB_ID:
            raise AuthenticationError('请先在企业咕咚中进入浙江大学线上运动平台。')
        client.user_id = profile['user_id']
        school = client.request('GET', '/v1/club_custom_activity/48472/detail', {'club_id': CLUB_ID})
        # Authentication is verified by the server, not by trusting decoded JWT claims.
        return {'access_token': token, 'user_id': profile['user_id'], 'club_id': CLUB_ID}, school
    finally:
        client.close()


def login(store, manual=False):
    with store.lock:
        if manual:
            session, school = verify(getpass.getpass('企业咕咚 Token（隐藏输入）：'))
        else:
            print('正在读取当前用户的企业咕咚会话…', flush=True)
            sessions = {}
            for candidate in read_sessions():
                try:
                    session, school = verify(candidate)
                except AuthenticationError:
                    continue
                previous = sessions.get(session['user_id'])
                if previous is None or expiration(session['access_token']) > expiration(previous[0]['access_token']):
                    sessions[session['user_id']] = session, school
            choices = list(sessions.values())
            if not choices:
                raise AuthenticationError('未找到有效的浙大企业咕咚会话。请在微信中重新进入该小程序，或使用 login --token。')
            if len(choices) == 1:
                session, school = choices[0]
            else:
                for index, (item, _) in enumerate(choices, 1):
                    print(f'{index}. 浙大账号 …{str(item["user_id"])[-6:]}')
                try:
                    selected = int(input('选择要登录的账号：'))
                except ValueError as error:
                    raise AuthenticationError('请输入列表中的账号编号。') from error
                if not 1 <= selected <= len(choices):
                    raise AuthenticationError('账号编号超出列表范围。')
                session, school = choices[selected - 1]
        store.save('session.json', session)
        print(f'登录成功：浙江大学线上运动平台，当前 {school["total_steps"]}/{school["total_steps_limit"]} 次。')


def authenticated_client(store):
    session = store.load('session.json')
    if session is None:
        raise AuthenticationError('尚未登录，请先运行 login。')
    if session['club_id'] != CLUB_ID:
        raise AuthenticationError('本版本支持浙江大学线上运动平台，请重新 login。')
    token = normalize_token(session['access_token'])
    if expiration(token) <= time.time() + 3600:
        raise AuthenticationError('登录态即将过期，请重新运行 login。')
    return Client(token, session['user_id'])
