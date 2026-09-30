"""Session decoding plus a native parent/child memory-read smoke test where permitted."""
import base64
import json
import subprocess
import sys
import unittest

from godoon.sessions import LinuxMemory, MacMemory, tokens_from_chunk


def fake_token():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return '.'.join((encode({'alg': 'HS256'}), encode({'exp': 4102444800, 'sub': 'test-only'}), 'x' * 43))


class SessionTests(unittest.TestCase):
    def test_ascii_and_utf16_sessions_are_read_without_exposing_other_strings(self):
        token = fake_token()
        # Align the UTF-16 input separately, as real VM chunks start on page boundaries.
        self.assertEqual(tokens_from_chunk(('Bearer ' + token).encode()), {token})
        self.assertEqual(tokens_from_chunk(('Bearer ' + token).encode('utf-16-le')), {token})
        self.assertEqual(tokens_from_chunk(b'not a bearer token'), set())

    @unittest.skipUnless(sys.platform in ('linux', 'darwin'), 'Native Unix reader test')
    def test_native_reader_can_read_a_consented_child_buffer(self):
        script = "import ctypes,sys; b=ctypes.create_string_buffer(sys.argv[1].encode()); print('ready',flush=True); sys.stdin.readline()"
        token = fake_token()
        process = subprocess.Popen([sys.executable, '-c', script, 'mini-club.codoon.com Bearer ' + token],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            try:
                reader = (LinuxMemory if sys.platform == 'linux' else MacMemory)(process.pid)
            except PermissionError:
                self.skipTest('OS denied debugging a child; manual Token login remains available')
            try:
                self.assertTrue(any(token in tokens_from_chunk(chunk) for chunk in reader.chunks()))
            except PermissionError:
                self.skipTest('OS denied process memory access')
            finally:
                reader.close()
        finally:
            process.communicate('\n', timeout=10)


if __name__ == '__main__':
    unittest.main()
