#!/usr/bin/env python3
"""
飞书应用注册脚本 — 扫码即可创建 PersonalAgent 应用并获取 App ID / App Secret
Usage: python system/feishu_register.py
"""
import qrcode
from pathlib import Path
import lark_oapi as lark


def on_qr_code(info: dict):
    url = info["url"]
    expire = info.get("expire_in", 600)

    print(f"\n请用飞书扫描下方二维码授权（有效期 {expire} 秒）：\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print(f"\n（或复制链接在浏览器打开）：{url}\n")


def on_status_change(info: dict):
    status = info.get("status", "")
    if status == "polling":
        print("⏳ 等待扫码...")
    elif status == "domain_switched":
        print("🔄 切换到 Lark 域...")
    elif status == "slow_down":
        print(f"⚠️  请求过快，减速至每 {info.get('interval', 10)} 秒一次...")


def main():
    print("=" * 50)
    print("飞书应用一键创建工具")
    print("=" * 50)

    result = lark.register_app(
        on_qr_code=on_qr_code,
        on_status_change=on_status_change,
    )

    app_id = result["client_id"]
    app_secret = result["client_secret"]

    print("\n✅ 应用创建成功！")
    print(f"  App ID:     {app_id}")
    print(f"  App Secret: {app_secret}")

    # 写入 infra/.env
    env_path = Path(__file__).parent.parent / "infra" / ".env"
    env_text = env_path.read_text() if env_path.exists() else ""

    for key, val in [("FEISHU_APP_ID", app_id), ("FEISHU_APP_SECRET", app_secret)]:
        if f"{key}=" in env_text:
            lines = env_text.splitlines()
            env_text = "\n".join(
                f"{key}={val}" if line.startswith(f"{key}=") else line
                for line in lines
            ) + "\n"
        else:
            env_text += f"\n{key}={val}\n"

    env_path.write_text(env_text)
    print(f"\n✅ 凭证已写入 {env_path}")
    print("\n下一步：在飞书开放平台开启 im:message 权限，然后运行 ./start.sh 启动机器人")


if __name__ == "__main__":
    main()
