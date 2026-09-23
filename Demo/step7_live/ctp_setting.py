"""Step 7: ctp_setting —— SimNow 7×24 仿真环境配置。

⚠️ 用户名/密码请从 ~/quantllm/SIMNOW模拟平台账户密码.txt 读取，
    不要硬编码到 git 里。

端点（不是 market1.simnow.com.cn，老版本 vnpy_ctp 用错域名连不上）：
    行情前置：tcp://182.254.243.31:40011
    交易前置：tcp://182.254.243.31:40001

DLL：vnpy_ctp 自带的 thostmduserapi_se.dll / thosttraderapi_se.dll 在新版 ubuntu 编译不过，
    改用 openctp-ctp（github.com/openctp/openctp-ctp-python）的 dll：
        - thostmduserapi_se.so（libthostmduserapi_se.so）
        - thosttraderapi_se.so（libthosttraderapi_se.so）
    放到 Python 能找到的路径（site-packages 或者 LD_LIBRARY_PATH 指向的目录）。
"""

# SimNow 7×24 仿真环境（不在交易时段时仍能登录、收报单）
TD_FRONT = "tcp://182.254.243.31:40001"
MD_FRONT = "tcp://182.254.243.31:40011"

# 经纪公司代码（SimNow 固定）
BROKER_ID = "9999"

# 从文件读账号 / 密码（不要写死在 git 里）
import os
from pathlib import Path

_CRED_FILE = Path.home() / "quantllm" / "SIMNOW模拟平台账户密码.txt"


def load_credentials() -> tuple[str, str, str]:
    """从 ~/quantllm/SIMNOW模拟平台账户密码.txt 读账号/密码/appid/auth_code。

    文件格式（UTF-8）：
        第一行：user_id
        第二行：password
        第三行：appid（可空）
        第四行：auth_code（可空）

    Returns:
        (user_id, password, appid, auth_code) 4 元组
    """
    if not _CRED_FILE.exists():
        raise FileNotFoundError(
            f"找不到凭证 {_CRED_FILE}。请确认文件存在。\n"
            f"格式：第 1 行 user_id，第 2 行 password，第 3 行 appid，第 4 行 auth_code"
        )
    lines = _CRED_FILE.read_text(encoding="utf-8").strip().splitlines()
    while len(lines) < 4:
        lines.append("")
    user_id = lines[0].strip()
    password = lines[1].strip()
    appid = lines[2].strip() if len(lines) > 2 else ""
    auth_code = lines[3].strip() if len(lines) > 3 else ""
    if not user_id or not password:
        raise ValueError(f"{_CRED_FILE} 里 user_id/password 不能为空")
    return user_id, password, appid, auth_code


def build_setting() -> dict:
    """构造 ctp_gateway.connect() 需要的 setting 字典。"""
    user_id, password, appid, auth_code = load_credentials()
    return {
        "用户名": user_id,
        "密码": password,
        "经纪公司代码": BROKER_ID,
        "交易服务器": TD_FRONT,
        "行情服务器": MD_FRONT,
        "产品名称": "quantllm",
        "授权编码": auth_code,
        "产品信息": appid,
    }