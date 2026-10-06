#!/usr/bin/env python3
"""GitHub Actions Telegram worker for the Zephyr Kernel Builder.

This worker is intentionally short-lived. GitHub Actions starts it on a
5-minute schedule, it consumes pending Telegram updates, performs any requested
GitHub Actions operation, confirms the updates, and exits. No external server
or persistent bot process is required.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "almightygodthor/zephyr-kernel-builder")
WORKFLOW = os.environ.get("GITHUB_WORKFLOW", "build.yml")
BRANCH = os.environ.get("GITHUB_REF", "main")

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
GH_API = "https://api.github.com"


class APIError(RuntimeError):
    pass


def http_json(url, method="GET", data=None, headers=None, timeout=30):
    body = None
    hdrs = {"User-Agent": "Zephyr-GitHub-Telegram-Worker"}
    if headers:
        hdrs.update(headers)
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise APIError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise APIError(f"network error: {exc.reason}") from exc


def tg(method, data=None, timeout=20):
    result = http_json(
        f"{TG_API}/{method}", method="POST", data=data, timeout=timeout
    )
    if not result.get("ok"):
        raise APIError(result.get("description", f"Telegram {method} failed"))
    return result.get("result")


def gh(method, path, data=None):
    if not GH_TOKEN:
        raise APIError("GITHUB_TOKEN is not available")
    return http_json(
        f"{GH_API}{path}",
        method=method,
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GH_TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
        },
        timeout=30,
    )


def esc(value):
    return html.escape(str(value), quote=False)


def send(chat_id, text, keyboard=None, message_id=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if message_id is not None:
        payload["message_id"] = message_id
        return tg("editMessageText", payload)
    return tg("sendMessage", payload)


def answer_callback(query_id, text="", show_alert=False):
    try:
        tg(
            "answerCallbackQuery",
            {
                "callback_query_id": query_id,
                "text": text,
                "show_alert": show_alert,
            },
        )
    except Exception as exc:
        print(f"callback answer failed: {exc}", file=sys.stderr)


def is_admin(chat_id, user_id):
    try:
        member = tg("getChatMember", {"chat_id": chat_id, "user_id": user_id})
        return member and member.get("status") in {"administrator", "creator"}
    except Exception as exc:
        print(f"admin check failed: {exc}", file=sys.stderr)
        return False


def require_admin(chat_id, user_id):
    if is_admin(chat_id, user_id):
        return True
    send(chat_id, "⛔ <b>Admin only.</b>\nOnly Telegram group administrators can use the build bot.")
    return False


def menu_text():
    return (
        "<b>⚡ ZEPHYR KERNEL BUILDER</b>\n\n"
        "📱 <b>Realme GT Neo 3</b>\n"
        "└─ zephyr / MT6895\n"
        "🧩 Kernel <b>5.10</b>\n\n"
        "Choose an action:"
    )


def menu_keyboard():
    return [
        [{"text": "🔨 Build Kernel", "callback_data": "build"}],
        [{"text": "📊 Current Build", "callback_data": "status"}],
    ]


def root_keyboard():
    return [
        [{"text": "🌳 KernelSU-Next", "callback_data": "root:ksu-next"}],
        [{"text": "⚪ No Root", "callback_data": "root:none"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}],
    ]


def susfs_keyboard(root):
    return [
        [{"text": "🛡 Enable SUSFS", "callback_data": f"susfs:{root}:on"}],
        [{"text": "⚪ Disable SUSFS", "callback_data": f"susfs:{root}:off"}],
        [{"text": "◀️ Back", "callback_data": "build"}],
    ]


def confirm_keyboard(root, susfs):
    state = "on" if susfs else "off"
    return [
        [{"text": "🚀 START BUILD", "callback_data": f"confirm:{root}:{state}"}],
        [{"text": "✏️ Change", "callback_data": "build"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}],
    ]


def build_text(root, susfs):
    root_label = "KernelSU-Next" if root == "ksu-next" else "No Root"
    susfs_label = "Enabled" if susfs else "Disabled"
    return (
        "<b>⚡ ZEPHYR KERNEL BUILD</b>\n\n"
        "📱 <b>Device</b>\n└─ Realme GT Neo 3 / zephyr\n\n"
        "🧩 <b>Kernel</b>\n└─ Linux 5.10 / MT6895\n\n"
        f"🌳 <b>Root</b>\n└─ {root_label}\n"
        f"🛡 <b>SUSFS</b>\n└─ {susfs_label}\n"
        "📦 <b>AnyKernel3</b>\n└─ Enabled\n\n"
        "Confirm this build configuration:"
    )


def active_run():
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/runs?event=workflow_dispatch&branch={urllib.parse.quote(BRANCH, safe='')}&per_page=20",
    )
    for run in data.get("workflow_runs", []):
        if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
            return run
    return None


def dispatch_build(root, susfs):
    active = active_run()
    if active:
        return None, active

    gh(
        "POST",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(WORKFLOW, safe='')}/dispatches",
        {
            "ref": BRANCH,
            "inputs": {
                "root": root,
                "susfs": "true" if susfs else "false",
                "build_ak3": "true",
            },
        },
    )

    data = gh(
        "GET",
        f"/repos/{REPO}/actions/runs?event=workflow_dispatch&branch={urllib.parse.quote(BRANCH, safe='')}&per_page=10",
    )
    runs = data.get("workflow_runs", [])
    return (runs[0] if runs else None), None


def run_status_text(run):
    status = run.get("status", "unknown").replace("_", " ").title()
    conclusion = run.get("conclusion")
    if conclusion:
        status = f"{status} / {conclusion.title()}"
    return status


def status_message(run):
    return (
        "<b>📊 ZEPHYR BUILD STATUS</b>\n\n"
        f"🆔 <b>Run</b>: #{esc(run.get('run_number', '?'))}\n"
        f"⚙️ <b>Status</b>: {esc(run_status_text(run))}\n"
        f"🌿 <b>Branch</b>: <code>{esc(BRANCH)}</code>"
    )


def status_keyboard(run):
    buttons = []
    if run.get("html_url"):
        buttons.append([{"text": "🔗 GitHub Actions", "url": run["html_url"]}])
    buttons.append([{"text": "🛑 Cancel Build", "callback_data": f"cancelrun:{run['id']}"}])
    return buttons


def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    command = text.split()[0].split("@")[0].lower()
    if command in {"/start", "/kernel"}:
        if require_admin(chat_id, user_id):
            send(chat_id, menu_text(), menu_keyboard())
    elif command == "/status":
        if not require_admin(chat_id, user_id):
            return
        run = active_run()
        if run:
            send(chat_id, status_message(run), status_keyboard(run))
        else:
            send(chat_id, "🟢 <b>No build is currently running.</b>", menu_keyboard())
    elif command == "/cancel":
        if not require_admin(chat_id, user_id):
            return
        run = active_run()
        if not run:
            send(chat_id, "🟢 <b>No build is currently running.</b>")
            return
        try:
            gh("POST", f"/repos/{REPO}/actions/runs/{run['id']}/cancel")
            send(chat_id, f"🛑 <b>Cancellation requested.</b>\nRun #{esc(run.get('run_number', '?'))}")
        except Exception as exc:
            send(chat_id, f"❌ <b>Cancel failed</b>\n<code>{esc(exc)}</code>")


def handle_callback(query):
    query_id = query.get("id")
    data = query.get("data", "")
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user_id = (query.get("from") or {}).get("id")
    message_id = message.get("message_id")

    answer_callback(query_id, "Processing…")

    if not is_admin(chat_id, user_id):
        answer_callback(query_id, "Admin only.", show_alert=True)
        return

    try:
        if data == "build":
            send(
                chat_id,
                "<b>🌳 Select Root Manager</b>\n\nChoose the root implementation for this build:",
                root_keyboard(),
                message_id,
            )
            return

        if data == "root:ksu-next":
            send(
                chat_id,
                "<b>🛡 Select SUSFS</b>\n\nKernelSU-Next selected.",
                susfs_keyboard("ksu-next"),
                message_id,
            )
            return

        if data == "root:none":
            send(
                chat_id,
                build_text("none", False),
                confirm_keyboard("none", False),
                message_id,
            )
            return

        if data.startswith("susfs:"):
            _, root, state = data.split(":", 2)
            if root != "ksu-next" or state not in {"on", "off"}:
                raise APIError("invalid SUSFS selection")
            susfs = state == "on"
            send(
                chat_id,
                build_text(root, susfs),
                confirm_keyboard(root, susfs),
                message_id,
            )
            return

        if data.startswith("confirm:"):
            _, root, state = data.split(":", 2)
            if root not in {"ksu-next", "none"} or state not in {"on", "off"}:
                raise APIError("invalid build selection")
            susfs = root == "ksu-next" and state == "on"
            run, existing = dispatch_build(root, susfs)
            if existing:
                send(
                    chat_id,
                    f"⚠️ <b>A build is already running.</b>\nRun #{esc(existing.get('run_number', '?'))}",
                    status_keyboard(existing),
                    message_id,
                )
                return
            if not run:
                send(
                    chat_id,
                    "⚠️ <b>Build was dispatched, but GitHub has not created the run yet.</b>\nUse /status in a moment.",
                    menu_keyboard(),
                    message_id,
                )
                return
            rows = []
            if run.get("html_url"):
                rows.append([{"text": "🔗 GitHub Actions", "url": run["html_url"]}])
            rows.append([{"text": "📊 Status", "callback_data": "status"}])
            send(
                chat_id,
                (
                    "🚀 <b>BUILD QUEUED</b>\n\n"
                    "📱 Realme GT Neo 3 / zephyr\n"
                    f"🌳 Root: <code>{esc('KernelSU-Next' if root == 'ksu-next' else 'No Root')}</code>\n"
                    f"🛡 SUSFS: <code>{esc('Enabled' if susfs else 'Disabled')}</code>\n"
                    f"🆔 Run: <code>#{esc(run.get('run_number', '?'))}</code>"
                ),
                rows,
                message_id,
            )
            return

        if data == "status":
            run = active_run()
            if run:
                send(chat_id, status_message(run), status_keyboard(run), message_id)
            else:
                send(chat_id, "🟢 <b>No build is currently running.</b>", menu_keyboard(), message_id)
            return

        if data.startswith("cancelrun:"):
            run_id = data.split(":", 1)[1]
            gh("POST", f"/repos/{REPO}/actions/runs/{run_id}/cancel")
            send(chat_id, "🛑 <b>Build cancellation requested.</b>", menu_keyboard(), message_id)
            return

        if data == "cancel":
            send(chat_id, menu_text(), menu_keyboard(), message_id)
            return

    except Exception as exc:
        print(f"callback {data!r} failed: {exc}", file=sys.stderr)
        send(chat_id, f"❌ <b>Action failed</b>\n<code>{esc(exc)}</code>", menu_keyboard(), message_id)


def process_updates(updates):
    for update in updates:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])


def main():
    print("Zephyr GitHub Telegram worker started", flush=True)

    try:
        tg("deleteWebhook", {"drop_pending_updates": False})
    except Exception as exc:
        print(f"deleteWebhook warning: {exc}", file=sys.stderr)

    try:
        updates = tg(
            "getUpdates",
            {
                "offset": 0,
                "timeout": 5,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=12,
        ) or []

        if not updates:
            print("No pending Telegram updates.", flush=True)
            return 0

        print(f"Processing {len(updates)} Telegram update(s).", flush=True)
        process_updates(updates)

        last_id = max(update["update_id"] for update in updates)
        tg(
            "getUpdates",
            {
                "offset": last_id + 1,
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=5,
        )
        print(f"Confirmed updates through {last_id}.", flush=True)
        return 0
    except Exception as exc:
        print(f"worker failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
