#!/usr/bin/env python3
import html
import json
import os
import sys
import urllib.parse
import urllib.request


def api(method: str, fields: dict) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def send(text: str, keyboard=None) -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        print("Telegram chat ID not configured; skipping notification.")
        return
    fields = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        fields["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    result = api("sendMessage", fields)
    if result and not result.get("ok", False):
        raise RuntimeError(result.get("description", "Telegram API error"))


def main() -> int:
    state = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    run_id = os.environ.get("RUN_ID", "-")
    run_number = os.environ.get("RUN_NUMBER", "-")
    sha = html.escape(os.environ.get("SHA", "-")[:12])
    owner_repo = os.environ.get("GITHUB_REPOSITORY", "almightygodthor/zephyr-kernel-builder")
    url = f"https://github.com/{owner_repo}/actions/runs/{run_id}"
    root = os.environ.get("ROOT_IMPL", "ksu-next")
    root_label = "KernelSU-Next" if root == "ksu-next" else "No Root"
    susfs = os.environ.get("SUSFS_ENABLED", "false").lower() == "true"
    susfs_label = "Enabled" if susfs else "Disabled"
    release_url = os.environ.get("RELEASE_URL", "")
    artifact_name = os.environ.get("ARTIFACT_NAME", "")
    artifact_size = os.environ.get("ARTIFACT_SIZE", "")
    artifact_sha256 = os.environ.get("ARTIFACT_SHA256", "")
    icon = {"started": "🚀", "success": "✅", "failure": "❌"}.get(state, "ℹ️")

    if state == "success":
        body = (
            "<b>╭────────────────────────╮</b>\n"
            "<b>│    ⚡ ZEPHYR KERNEL    │</b>\n"
            "<b>│      BUILD COMPLETE    │</b>\n"
            "<b>╰────────────────────────╯</b>\n\n"
            "📱 <b>Device</b>\n└─ Realme GT Neo 3 / zephyr / MT6895\n\n"
            "🧩 <b>Kernel</b>\n└─ Linux 5.10 / lineage-24.0\n\n"
            "🔧 <b>Configuration</b>\n"
            f" • Root: <code>{html.escape(root_label)}</code>\n"
            f" • SUSFS: <code>{susfs_label}</code>\n"
            " • AnyKernel3: <code>Enabled</code>\n\n"
            "📦 <b>Artifact</b>\n"
            f" • File: <code>{html.escape(artifact_name or 'see GitHub Release')}</code>\n"
            f" • Size: <code>{html.escape(artifact_size or '-')}</code>\n"
            f" • SHA256: <code>{html.escape(artifact_sha256 or '-')}</code>\n\n"
            f"🆔 Run: <code>#{html.escape(run_number)}</code>\n"
            f"📝 Commit: <code>{sha}</code>"
        )
        keyboard = []
        if release_url:
            keyboard.append([{"text": "📦 GitHub Release", "url": release_url}])
        keyboard.append([
            {"text": "🌳 KSU-Next", "url": "https://github.com/KernelSU-Next/KernelSU-Next/releases"},
            {"text": "🛡 SUSFS Module", "url": "https://github.com/sidex15/susfs4ksu-module/releases"},
        ])
    else:
        body = (
            f"{icon} <b>Zephyr Kernel Build</b>\n\n"
            "📱 Device: <code>Realme GT Neo 3 / zephyr</code>\n"
            "🧩 Kernel: <code>5.10 / MT6895</code>\n"
            f"🌳 Root: <code>{html.escape(root_label)}</code>\n"
            f"🛡 SUSFS: <code>{susfs_label}</code>\n"
            f"🆔 Run: <code>#{html.escape(run_number)}</code>\n"
            f"📝 Commit: <code>{sha}</code>"
        )
        keyboard = [[{"text": "🔗 GitHub Actions", "url": url}]]
    send(body, keyboard)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
