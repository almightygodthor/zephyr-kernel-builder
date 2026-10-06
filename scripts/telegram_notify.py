#!/usr/bin/env python3
import os
import sys
import urllib.parse
import urllib.request


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram secrets not configured; skipping notification.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"Telegram API returned HTTP {response.status}")


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    run_id = os.environ.get("RUN_ID", "-")
    run_number = os.environ.get("RUN_NUMBER", "-")
    sha = os.environ.get("SHA", "-")[:12]
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "almightygodthor/zephyr-kernel-builder")
    url = f"https://github.com/{owner_repo}/actions/runs/{run_id}"
    icon = {"started": "🚀", "success": "✅", "failure": "❌"}.get(state, "ℹ️")
    body = (
        f"{icon} Zephyr Kernel Build\\n\\n"
        f"Device: Realme GT Neo 3\\n"
        f"Kernel: 5.10 / MT6895\\n"
        f"Root: KernelSU-Next + SUSFS\\n"
        f"Run: #{run_number}\\n"
        f"Commit: {sha}\\n\\n"
        f"<a href=\"{url}\">GitHub Actions run</a>"
    )
    send(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
