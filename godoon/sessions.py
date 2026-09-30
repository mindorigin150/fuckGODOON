"""Read only the current OS user's Codoon mini-program session candidates."""
import ctypes
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

import psutil

HOST = b'mini-club.codoon.com'
HOST_UTF16 = HOST.decode().encode('utf-16-le')
TOKEN = re.compile(r'Bearer (eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43})(?![A-Za-z0-9_-])')
CHUNK = 1024 * 1024
OVERLAP = 4096


def tokens_from_chunk(chunk):
    tokens = set(TOKEN.findall(chunk.decode('ascii', errors='ignore')))
    tokens.update(TOKEN.findall(chunk[:len(chunk)//2*2].decode('utf-16-le', errors='ignore')))
    return tokens


def windows_sessions():
    executable = shutil.which('powershell.exe')
    if executable is None and sys.platform == 'linux' and 'microsoft' in platform.release().lower():
        # WSL's documented default Windows drive mount; no user-specific path.
        mounted = Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
        if mounted.is_file():
            executable = str(mounted)
    if executable is None:
        raise RuntimeError('未找到 Windows PowerShell，请使用 login --token。')
    script = Path(__file__).with_name('windows_session.ps1').read_text(encoding='utf-8')
    try:
        result = subprocess.run([executable, '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('微信会话读取超时，请重新进入小程序后重试。') from error
    if result.returncode:
        raise RuntimeError('无法读取当前用户的微信会话，可使用 login --token。')
    values = json.loads(result.stdout) if result.stdout.strip() else []
    return [values] if isinstance(values, str) else values


class LinuxMemory:
    """Read accessible private memory ranges through procfs without changing ptrace policy."""

    def __init__(self, pid):
        self.base = Path('/proc') / str(pid)

    def chunks(self):
        deadline = time.monotonic() + 20
        mappings = (self.base / 'maps').read_text().splitlines()
        with (self.base / 'mem').open('rb', buffering=0) as memory:
            for line in mappings:
                bounds, permissions, *_ = line.split()
                if not permissions.startswith('r') or not permissions.endswith('p'):
                    continue
                start, end = (int(value, 16) for value in bounds.split('-'))
                for address in range(start, end, CHUNK - OVERLAP):
                    if time.monotonic() > deadline:
                        raise TimeoutError('process memory scan')
                    try:
                        memory.seek(address)
                        yield memory.read(min(CHUNK, end-address))
                    except (OSError, OverflowError):
                        # Mappings may disappear while the renderer is running.
                        continue

    def close(self):
        pass


class MacRegion(ctypes.Structure):
    """Mach VM_REGION_BASIC_INFO_64 ABI, including its four-byte packing."""
    _pack_ = 4
    _fields_ = [('protection', ctypes.c_int), ('maximum', ctypes.c_int),
                ('inheritance', ctypes.c_int), ('shared', ctypes.c_uint),
                ('reserved', ctypes.c_uint), ('offset', ctypes.c_uint64),
                ('behavior', ctypes.c_int), ('wired', ctypes.c_ushort)]


class MacMemory:
    """Read a task only when macOS grants task_for_pid access; release its Mach rights."""

    def __init__(self, pid):
        self.lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        port = ctypes.c_uint
        address = ctypes.c_uint64
        self.lib.task_for_pid.argtypes = [port, ctypes.c_int, ctypes.POINTER(port)]
        self.lib.task_for_pid.restype = ctypes.c_int
        self.lib.mach_vm_region.argtypes = [port, ctypes.POINTER(address), ctypes.POINTER(address),
                                            ctypes.c_int, ctypes.POINTER(MacRegion),
                                            ctypes.POINTER(port), ctypes.POINTER(port)]
        self.lib.mach_vm_region.restype = ctypes.c_int
        self.lib.mach_vm_read_overwrite.argtypes = [port, address, address, address, ctypes.POINTER(address)]
        self.lib.mach_vm_read_overwrite.restype = ctypes.c_int
        self.lib.mach_port_deallocate.argtypes = [port, port]
        self.lib.mach_port_deallocate.restype = ctypes.c_int
        self.me = port.in_dll(self.lib, 'mach_task_self_').value
        self.task = port()
        if self.lib.task_for_pid(self.me, pid, ctypes.byref(self.task)) != 0:
            raise PermissionError('macOS denied task_for_pid')

    def chunks(self):
        deadline = time.monotonic() + 20
        address = ctypes.c_uint64(0)
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('process memory scan')
            size, info = ctypes.c_uint64(), MacRegion()
            count, object_port = ctypes.c_uint(9), ctypes.c_uint()
            code = self.lib.mach_vm_region(self.task, ctypes.byref(address), ctypes.byref(size),
                                           9, ctypes.byref(info), ctypes.byref(count), ctypes.byref(object_port))
            if object_port.value:
                self.lib.mach_port_deallocate(self.me, object_port)
            if code != 0 or size.value == 0:
                return
            end = address.value + size.value
            if info.protection & 1 and not info.shared:
                for location in range(address.value, end, CHUNK - OVERLAP):
                    if time.monotonic() > deadline:
                        raise TimeoutError('process memory scan')
                    length = min(CHUNK, end-location)
                    buffer, read = ctypes.create_string_buffer(length), ctypes.c_uint64()
                    code = self.lib.mach_vm_read_overwrite(self.task, location, length,
                                                          ctypes.addressof(buffer), ctypes.byref(read))
                    if code == 0 and read.value:
                        yield buffer.raw[:read.value]
            address.value = end

    def close(self):
        self.lib.mach_port_deallocate(self.me, self.task)


def read_sessions():
    if sys.platform == 'win32' or (sys.platform == 'linux' and 'microsoft' in platform.release().lower()):
        return windows_sessions()
    if sys.platform not in ('linux', 'darwin'):
        raise RuntimeError('此系统请使用 login --token。')
    reader_type = LinuxMemory if sys.platform == 'linux' else MacMemory
    tokens = set()
    denied = False
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if process.uids().real != os.getuid():
                continue
            if not process.info['name'] or process.info['cmdline'] is None:
                continue
            name = process.info['name'].lower()
            command = ' '.join(process.info['cmdline']).lower()
            is_wechat = 'wechat' in name or 'weixin' in name
            if not is_wechat or not ('appex' in name or '--type=renderer' in command):
                continue
            reader = reader_type(process.pid)
            try:
                # First locate the Codoon renderer, then inspect only that process for sessions.
                if any(HOST in chunk or HOST_UTF16 in chunk for chunk in reader.chunks()):
                    for chunk in reader.chunks():
                        tokens.update(tokens_from_chunk(chunk))
            finally:
                reader.close()
        except (PermissionError, psutil.AccessDenied):
            denied = True
        except (OSError, psutil.NoSuchProcess):
            continue
    if not tokens and denied:
        raise RuntimeError('系统未允许读取微信小程序进程。可使用 login --token 导入自己的登录态。')
    return sorted(tokens)
