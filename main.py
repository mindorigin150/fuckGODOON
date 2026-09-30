"""Two commands: import the existing WeChat session, then start a run."""
import argparse
from pathlib import Path

from filelock import Timeout

from godoon.auth import login
from godoon.runner import start
from godoon.storage import Store


def main():
    parser = argparse.ArgumentParser(prog='fuckGODOON', description='浙大企业咕咚工具')
    commands = parser.add_subparsers(dest='command', required=True)
    login_parser = commands.add_parser('login', help='读取并验证已登录的微信会话')
    login_parser.add_argument('--token', action='store_true', help='通过隐藏输入导入自己的 Token')
    commands.add_parser('start', help='启动一次运行；今天已完成时自动跳过')
    args = parser.parse_args()
    store = Store(Path.home() / '.fuckgodoon')
    try:
        if args.command == 'login':
            login(store, manual=args.token)
        else:
            start(store, Path(__file__).resolve().with_name('route.json'))
    except Timeout:
        parser.exit(1, '已有任务正在运行，请先结束当前任务。\n')
    except (KeyboardInterrupt, InterruptedError) as error:
        print(str(error) or '已取消。')
    except (RuntimeError, OSError, ValueError, KeyError, EOFError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
