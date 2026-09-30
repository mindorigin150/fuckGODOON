"""Keep credentials and the owned run journal in the current user's directory."""
import json
import os
from pathlib import Path
import tempfile

from filelock import FileLock


class Store:
    """Own private JSON files and a cross-platform single-run lock."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != 'nt':
            directory.chmod(0o700)
        self.lock = FileLock(directory / 'run.lock', timeout=0)

    def load(self, name: str):
        path = self.directory / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as error:
            raise RuntimeError(f'无法读取本地文件 {path.name}，请检查后重试。') from error

    def save(self, name: str, value):
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix='.tmp-')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.directory / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
