"""Step 8: ctp_setting - SimNow 7x24 + DLL path.

凭证从 ~/quantllm/SIMNOW...txt 读，不写死到 git。
"""

import os
from pathlib import Path

# SimNow 7x24
TD_FRONT = "tcp://182.254.243.31:40001"
MD_FRONT = "tcp://182.254.243.31:40011"
BROKER_ID = "9999"


def setup_dll_path() -> None:
    """把 openctp-ctp 的 .so 加到 LD_LIBRARY_PATH。"""
    candidates = [
        Path.home() / "openctp-ctp" / "linux",
        Path("/usr/local/lib"),
        Path("/usr/lib"),
    ]
    added = []
    for p in candidates:
        if p.exists() and any(p.glob("*thost*api*.so*")):
            os.environ["LD_LIBRARY_PATH"] = str(p) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
            added.append(str(p))
    if added:
        print(f"[cta_setting] LD_LIBRARY_PATH 加上：{added}")


_CRED_FILE = Path.home() / "quantllm" / "SIMNOW模拟平台账户密码.txt"


def load_credentials() -> dict:
    """读 4 行：user_id / password / appid / auth_code。"""
    if not _CRED_FILE.exists():
        raise FileNotFoundError(
            f"找不到凭证 {_CRED_FILE}。\n"
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
    return {
        "用户名": user_id,
        "密码": password,
        "经纪商代码": BROKER_ID,
        "交易服务器": TD_FRONT,
        "行情服务器": MD_FRONT,
        "产品名称": "quantllm",
        "授权编码": auth_code,
        "产品信息": appid,
    }
