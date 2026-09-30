"""Authenticated requests to the same backend used by the mini-program."""
import base64
import json

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

BASE = 'https://mini-club.codoon.com'
CLUB_ID = 48472


class AuthenticationError(RuntimeError):
    """The supplied session is missing, expired or rejected by the backend."""


def decode_response(result):
    if 'codoon_key' not in result:
        return result
    key = ('GUYHDB3V' + result['codoon_key']).encode()
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    plaintext = decryptor.update(base64.b64decode(result['codoon_data'])) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return json.loads(unpadder.update(plaintext) + unpadder.finalize())


class Client:
    """Bind requests to one verified member session; never forward credentials to redirects."""

    def __init__(self, token: str, user_id=None):
        self.token = token
        self.user_id = user_id
        self.http = requests.Session()
        self.http.headers.update(Authorization='Bearer ' + token, **{'User-Agent': 'fuckGODOON/0.1'})

    def request(self, method, path, data):
        values = dict(data)
        values.update(platform_source_type=0, platform_language='zh')
        if self.user_id is not None:
            values['platform_user_id'] = self.user_id
        arguments = {'params': values} if method == 'GET' else {'json': values}
        try:
            response = self.http.request(method, BASE + path, timeout=30,
                                         allow_redirects=False, **arguments)
        except requests.RequestException as error:
            raise RuntimeError('无法连接企业咕咚，请检查网络。') from error
        if response.status_code == 401:
            raise AuthenticationError('登录态已失效，请重新运行 login。')
        if response.status_code != 200:
            message = f'{method} {path}: HTTP {response.status_code}'
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and isinstance(body.get('detail'), str):
                message += ': ' + body['detail'].replace(self.token, '[redacted]')[:200]
            raise RuntimeError(message)
        try:
            return decode_response(response.json())
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError('无法解析企业咕咚响应，接口可能已变化。') from error

    def close(self):
        self.http.close()
