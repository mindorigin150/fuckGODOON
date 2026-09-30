# fuckGODOON

浙大企业咕咚命令行工具：复用已登录的微信会话，生成并上传跑步记录，再核对活动有效次数。支持 Windows、Linux、macOS；WSL 可读取宿主 Windows 的微信会话。

## 使用

需要 **64 位 Python 3.10+**，并已在企业咕咚中绑定浙大学号、进入浙江大学线上运动平台。

```sh
git clone https://github.com/mindorigin150/fuckGODOON.git
cd fuckGODOON
python -m venv .venv
```

激活环境：Linux/macOS 用 `source .venv/bin/activate`；Windows CMD 用 `.venv\Scripts\activate.bat`。Linux/macOS 上可将 `python` 换成 `python3`。

```sh
python -m pip install -r requirements.txt
```

在电脑微信中打开**企业咕咚**，然后：

```sh
python main.py login
python main.py start
```

只有这两个命令。默认玉泉校区路线、**3.1 公里、约 22 分钟**；当天已有有效计分会自动跳过。`Ctrl+C` 停止并清理本工具未完成的记录，已保存记录会保留。

## 说明

- `login` 只读取当前系统用户的微信小程序进程，并向企业咕咚验证账号。自动读取受微信版本及系统进程权限限制；失败时可用 `python main.py login --token` 隐藏输入自己的 Token。
- 登录态保存在用户目录 `~/.fuckgodoon/`，不进入仓库。过期后，在微信重新进入企业咕咚并再次 `login`。
- 运行时保持电脑联网和唤醒。上传完成后等待活动统计刷新，最多约 31 分钟；只有记录有效且活动次数增加才报告成功。
- 路线、距离和速度在 `route.json` 中修改。其他校区应使用相应校内路线。

测试：`python -m unittest discover -s tests -v`。CI 覆盖 Windows、Linux、macOS，不连接真实账号或创建运动记录。

## 许可

代码采用 [MIT](LICENSE)。示例路线 © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，数据采用 ODbL；来源见 `route.json`。
