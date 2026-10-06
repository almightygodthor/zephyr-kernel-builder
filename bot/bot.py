#!/usr/bin/env python3
"""Long-lived GitHub Actions Telegram worker for the Zephyr Kernel Builder."""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "almightygodthor/zephyr-kernel-builder")
BUILD_WORKFLOW = "376656950"
BOT_WORKFLOW = "telegram-bot.yml"
RELEASE_WORKFLOW = "publish-release.yml"
BRANCH = os.environ.get("GITHUB_REF", "main")
DELETE_OWNER_ID = 7577854738

# Keep comfortably below GitHub's 6-hour GitHub-hosted job limit.
WORKER_SECONDS = 340 * 60
POLL_TIMEOUT = 2
MONITOR_INTERVAL = 1
GH_PROGRESS_INTERVAL = 2
PENDING_CONFIGS = {}
TRACKED_RUNS = {}
LAST_BOT_MESSAGES = {}
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

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
    result = http_json(f"{TG_API}/{method}", method="POST", data=data, timeout=timeout)
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
        try:
            result = tg("editMessageText", payload)
            LAST_BOT_MESSAGES[chat_id] = message_id
            return result
        except APIError as exc:
            if "message is not modified" not in str(exc).lower():
                print(f"message edit failed: {exc}", file=sys.stderr)
            return None

    return tg("sendMessage", payload)


def send_fresh(chat_id, text, keyboard=None):
    result = send(chat_id, text, keyboard)
    if isinstance(result, dict) and result.get("message_id"):
        LAST_BOT_MESSAGES[chat_id] = result["message_id"]
    return result


def edit_message(chat_id, message_id, text, keyboard=None):
    """Edit an existing Telegram message without ever creating a replacement."""
    if message_id is None:
        return send_fresh(chat_id, text, keyboard)
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        return tg("editMessageText", payload)
    except APIError as exc:
        # Telegram returns this when the selected screen is already identical.
        if "message is not modified" in str(exc).lower():
            return None
        raise


def answer_callback(query_id, text="", show_alert=False):
    try:
        tg("answerCallbackQuery", {
            "callback_query_id": query_id,
            "text": text,
            "show_alert": show_alert,
        })
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
    send_fresh(chat_id, "⛔ <b>Admin only.</b>\nOnly Telegram group administrators can use the build bot.")
    return False


def menu_text():
    return (
        "<b>⚡ ZEPHYR KERNEL BUILDER</b>\n\n"
        "📱 <b>Realme GT Neo 3</b> · zephyr / MT6895\n"
        "🧩 <b>Linux 5.10</b>\n\n"
        "Choose an action:"
    )


def menu_keyboard():
    return [
        [{"text": "🔨 Build Kernel", "callback_data": "build"}],
        [{"text": "📊 Current Build", "callback_data": "status"}],
    ]


def root_keyboard():
    return [
        [{"text": "🌱 KernelSU-Next", "callback_data": "root:ksu-next"}],
        [{"text": "⚪ No Root", "callback_data": "root:none"}],
        [{"text": "❌ Cancel", "callback_data": "cancel"}],
    ]


def susfs_keyboard(root):
    return [
        [{"text": "ඞ Enable SUSFS", "callback_data": f"susfs:{root}:on"}],
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
        "<b>⚡ BUILD CONFIG</b>\n\n"
        f"🌱 Root · <code>{root_label}</code>\n"
        f"ඞ SUSFS · <code>{susfs_label}</code>\n"
        "📦 AnyKernel3 · <code>Enabled</code>\n\n"
        "Ready to build?"
    )


def active_run():
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=20",
    )
    for run in data.get("workflow_runs", []):
        if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
            return run
    return None


def get_run(run_id):
    return gh("GET", f"/repos/{REPO}/actions/runs/{run_id}")

def release_for_run(run_number):
    try:
        return gh("GET", f"/repos/{REPO}/releases/tags/zephyr-run{run_number}")
    except APIError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise


def successful_build_runs(limit=10):
    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&status=success&per_page={limit}",
    )
    return data.get("workflow_runs", [])


def dispatch_release(run_id, run_number):
    return gh(
        "POST",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(RELEASE_WORKFLOW, safe='')}/dispatches",
        {
            "ref": BRANCH,
            "inputs": {
                "run_id": str(run_id),
                "run_number": str(run_number),
            },
            "return_run_details": True,
        },
    )


def dispatch_build(root, susfs):
    active = active_run()
    if active:
        return None, active

    result = gh(
        "POST",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/dispatches",
        {
            "ref": BRANCH,
            "inputs": {
                "root": root,
                "susfs": "true" if susfs else "false",
                "build_ak3": "true",
            },
            "return_run_details": True,
        },
    )

    run_id = result.get("workflow_run_id")
    if run_id:
        return gh("GET", f"/repos/{REPO}/actions/runs/{run_id}"), None

    data = gh(
        "GET",
        f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BUILD_WORKFLOW, safe='')}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=10",
    )
    runs = data.get("workflow_runs", [])
    return (runs[0] if runs else None), None


def status_message(run):
    status = run.get("status", "unknown").replace("_", " ").title()
    conclusion = run.get("conclusion")
    if conclusion:
        status = f"{status} / {conclusion.title()}"
    return (
        "<b>📊 ZEPHYR BUILD</b>\n\n"
        f"🆔 Run <code>#{esc(run.get('run_number', '?'))}</code>\n"
        f"⚙️ <code>{esc(status)}</code>\n"
        "📱 GT Neo 3 · zephyr\n"
        "🧩 Linux 5.10 · MT6895"
    )


def status_keyboard(run):
    buttons = []
    if run.get("html_url"):
        buttons.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])
    buttons.append([{"text": "🔄 Refresh", "callback_data": "status"}])
    if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"} and run.get("id"):
        buttons.append([{"text": "🛑 Cancel Build", "callback_data": f"cancelrun:{run['id']}"}])
    elif run.get("id") and run.get("conclusion"):
        buttons.append([{"text": "🗑️ Delete Build", "callback_data": f"deletebuild:{run['id']}"}])
    return buttons


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def job_progress(run):
    data = gh("GET", f"/repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")
    jobs = data.get("jobs", [])
    if not jobs:
        return "Starting runner…", 0, 1

    job = jobs[0]
    steps = job.get("steps") or []
    total = max(len(steps), 1)
    completed = sum(1 for s in steps if s.get("status") == "completed")
    current = next((s for s in steps if s.get("status") == "in_progress"), None)
    if current:
        name = current.get("name", "Working…")
        pct = min(99, int(((completed + 0.5) / total) * 100))
    elif job.get("status") in {"queued", "waiting", "requested", "pending"}:
        name = "Waiting for runner…"
        pct = 0
    else:
        name = steps[-1].get("name", "Finalizing…") if steps else "Starting…"
        pct = min(99, int((completed / total) * 100))

    labels = {
        "Checkout builder": "Preparing builder",
        "Install dependencies": "Installing dependencies",
        "Notify build started": "Starting build",
        "Install repo tool": "Preparing repo tool",
        "Sync Android kernel build manifest": "Syncing kernel source",
        "Inspect source tree": "Inspecting source",
        "Prepare Image.gz-only build config": "Configuring kernel",
        "Integrate KernelSU-Next and SUSFS": "Integrating KSU / SUSFS",
        "Build Image.gz": "Compiling Image.gz",
        "Verify Image.gz": "Verifying Image.gz",
        "Package AnyKernel3": "Packaging AnyKernel3",
        "Prepare release metadata": "Preparing release",
        "Create GitHub Release": "Publishing release",
        "Upload kernel artifacts": "Uploading artifacts",
        "Notify Telegram": "Finalizing",
    }
    return labels.get(name, name), pct, total


def elapsed_text(run):
    started = parse_time(run.get("run_started_at") or run.get("created_at"))
    if not started:
        return "--:--"
    seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def progress_message(run, root, susfs, frame, cached_stage=None, cached_pct=None):
    if cached_stage is not None and cached_pct is not None:
        stage, pct = cached_stage, cached_pct
    else:
        try:
            stage, pct, _ = job_progress(run)
        except Exception as exc:
            print(f"progress query failed: {exc}", file=sys.stderr)
            stage, pct = "Checking build progress…", 0

    blocks = 8
    filled = min(blocks, max(0, int(round(pct / 100 * blocks))))
    bar = "█" * filled + "░" * (blocks - filled)
    spin = SPINNER[frame % len(SPINNER)]
    root_label = "KSU-Next" if root == "ksu-next" else "No Root"
    susfs_label = "SUSFS" if susfs else "No SUSFS"

    return (
        f"<b>⚡ ZEPHYR · BUILDING {spin}</b>\n\n"
        "📱 GT Neo 3 · zephyr\n"
        "🧩 Linux 5.10 · MT6895\n"
        f"🌱 {root_label} · ඞ {susfs_label} · 📦 AK3\n\n"
        f"{spin} <b>{esc(stage)}</b>\n"
        f"<code>[{bar}] {pct}%</code>\n"
        f"⏱ {elapsed_text(run)} · 🆔 #{esc(run.get('run_number', '?'))}"
    )


def publish_keyboard(run_id, run_number):
    return [
        [{"text": "🚀 Publish GitHub Release", "callback_data": f"publish:{run_id}:{run_number}"}],
        [{"text": "🧪 No — Actions only", "callback_data": f"skip:{run_id}:{run_number}"}],
    ]


def release_list_keyboard(runs):
    rows = []
    for run in runs[:8]:
        rows.append([{
            "text": f"🚀 Run #{run.get('run_number', '?')} · Publish",
            "callback_data": f"publish:{run.get('id')}:{run.get('run_number')}",
        }])
    return rows


def progress_keyboard(run):
    rows = [[{"text": "🔄 Refresh", "callback_data": "status"}]]
    if run.get("html_url"):
        rows.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])
    rows.append([{"text": "🛑 Cancel Build", "callback_data": f"cancelrun:{run['id']}"}])
    return rows


def track_build(chat_id, message_id, run, root, susfs):
    TRACKED_RUNS[chat_id] = {
        "run_id": run["id"],
        "message_id": message_id,
        "root": root,
        "susfs": susfs,
        "frame": 0,
        "last_update": 0,
        "last_gh_update": 0,
        "stage": "Starting runner…",
        "pct": 0,
        "last_text": "",
    }


def monitor_builds():
    now = time.monotonic()
    for chat_id, item in list(TRACKED_RUNS.items()):
        if now - item["last_update"] < MONITOR_INTERVAL:
            continue
        item["last_update"] = now
        try:
            run = get_run(item["run_id"])
            if run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
                if now - item.get("last_gh_update", 0) >= GH_PROGRESS_INTERVAL:
                    try:
                        item["stage"], item["pct"], _ = job_progress(run)
                    except Exception as exc:
                        print(f"progress query failed: {exc}", file=sys.stderr)
                    item["last_gh_update"] = now

                text = progress_message(
                    run,
                    item["root"],
                    item["susfs"],
                    item["frame"],
                    item.get("stage", "Starting runner…"),
                    item.get("pct", 0),
                )
                item["frame"] += 1
                if text != item["last_text"]:
                    edit_message(chat_id, item["message_id"], text, progress_keyboard(run))
                    item["last_text"] = text
            else:
                TRACKED_RUNS.pop(chat_id, None)
        except Exception as exc:
            print(f"build monitor failed for chat {chat_id}: {exc}", file=sys.stderr)


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
            target_id = LAST_BOT_MESSAGES.get(chat_id)
            if target_id:
                edit_message(chat_id, target_id, menu_text(), menu_keyboard())
            else:
                send_fresh(chat_id, menu_text(), menu_keyboard())
    elif command == "/release":
        if not require_admin(chat_id, user_id):
            return
        runs = []
        for run in successful_build_runs():
            if not release_for_run(run.get("run_number")):
                runs.append(run)
        target_id = LAST_BOT_MESSAGES.get(chat_id)
        if runs:
            text = "<b>📦 PUBLISH A BUILD</b>\n\nChoose a successful build to publish:"
            kb = release_list_keyboard(runs)
        else:
            text = "🟢 <b>No unpublished successful builds found.</b>"
            kb = menu_keyboard()
        if target_id:
            edit_message(chat_id, target_id, text, kb)
        else:
            send_fresh(chat_id, text, kb)
    elif command == "/status":
        if not require_admin(chat_id, user_id):
            return
        tracked = TRACKED_RUNS.get(chat_id)
        run = None
        if tracked:
            try:
                run = get_run(tracked["run_id"])
            except Exception as exc:
                print(f"tracked run lookup failed: {exc}", file=sys.stderr)

        if run and run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
            text = progress_message(run, tracked["root"], tracked["susfs"], tracked["frame"])
            tracked["frame"] += 1
            edit_message(chat_id, tracked["message_id"], text, progress_keyboard(run))
            tracked["last_text"] = text
        else:
            run = active_run()
            target_id = tracked["message_id"] if tracked else LAST_BOT_MESSAGES.get(chat_id)
            if run:
                if target_id:
                    edit_message(
                        chat_id, target_id,
                        progress_message(run, "ksu-next", False, 0),
                        progress_keyboard(run),
                    )
                    track_build(chat_id, target_id, run, "ksu-next", False)
                else:
                    msg = send_fresh(
                        chat_id,
                        progress_message(run, "ksu-next", False, 0),
                        progress_keyboard(run),
                    )
                    track_build(chat_id, msg["message_id"], run, "ksu-next", False)
            elif target_id:
                edit_message(
                    chat_id, target_id,
                    "🟢 <b>No build is currently running.</b>",
                    menu_keyboard(),
                )
            else:
                send_fresh(chat_id, "🟢 <b>No build is currently running.</b>", menu_keyboard())

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
        edit_message(chat_id, message_id, "⛔ <b>Admin only.</b>")
        return

    try:
        if data == "build":
            edit_message(
                chat_id, message_id,
                "<b>🌱 SELECT ROOT</b>\n\nChoose the root implementation:",
                root_keyboard(),
            )
            return

        if data == "root:ksu-next":
            edit_message(
                chat_id, message_id,
                "<b>ඞ SELECT SUSFS</b>\n\nKernelSU-Next selected.",
                susfs_keyboard("ksu-next"),
            )
            return

        if data == "root:none":
            edit_message(
                chat_id, message_id,
                build_text("none", False),
                confirm_keyboard("none", False),
            )
            return

        if data.startswith("susfs:"):
            _, root, state = data.split(":", 2)
            if root != "ksu-next" or state not in {"on", "off"}:
                raise APIError("invalid SUSFS selection")
            susfs = state == "on"
            PENDING_CONFIGS[chat_id] = {"root": root, "susfs": susfs}
            edit_message(chat_id, message_id, build_text(root, susfs), confirm_keyboard(root, susfs))
            return

        if data.startswith("confirm:"):
            _, root, state = data.split(":", 2)
            if root not in {"ksu-next", "none"} or state not in {"on", "off"}:
                raise APIError("invalid build selection")

            pending = PENDING_CONFIGS.pop(chat_id, None)
            if pending:
                root = pending["root"]
                susfs = pending["susfs"]
            else:
                susfs = root == "ksu-next" and state == "on"

            run, existing = dispatch_build(root, susfs)
            if existing:
                edit_message(
                    chat_id, message_id,
                    f"⚠️ <b>Build already running</b> · #{esc(existing.get('run_number', '?'))}",
                    status_keyboard(existing),
                )
                return

            if not run:
                edit_message(
                    chat_id, message_id,
                    "⚠️ <b>Build dispatched</b>\nGitHub has not created the run yet.",
                    menu_keyboard(),
                )
                return

            rows = [[{"text": "🔄 Live Progress", "callback_data": "status"}]]
            if run.get("html_url"):
                rows.append([{"text": "🔗 GitHub Actions ↗", "url": run["html_url"]}])

            edit_message(
                chat_id, message_id,
                (
                    "<b>🚀 ZEPHYR · BUILD QUEUED</b>\n\n"
                    "📱 GT Neo 3 · zephyr\n"
                    f"🌱 {'KSU-Next' if root == 'ksu-next' else 'No Root'} · "
                    f"ඞ {'SUSFS' if susfs else 'No SUSFS'} · 📦 AK3\n"
                    f"🆔 Run <code>#{esc(run.get('run_number', '?'))}</code>"
                ),
                rows,
            )
            track_build(chat_id, message_id, run, root, susfs)
            return

        if data == "status":
            tracked = TRACKED_RUNS.get(chat_id)
            run = None
            if tracked:
                try:
                    run = get_run(tracked["run_id"])
                except Exception as exc:
                    print(f"tracked refresh lookup failed: {exc}", file=sys.stderr)

            if run and run.get("status") in {"queued", "in_progress", "waiting", "requested", "pending"}:
                text = progress_message(run, tracked["root"], tracked["susfs"], tracked["frame"])
                tracked["frame"] += 1
                edit_message(chat_id, tracked["message_id"], text, progress_keyboard(run))
                tracked["last_text"] = text
            else:
                run = active_run()
                if run:
                    edit_message(
                        chat_id, message_id,
                        progress_message(run, "ksu-next", False, 0),
                        progress_keyboard(run),
                    )
                    track_build(chat_id, message_id, run, "ksu-next", False)
                else:
                    edit_message(
                        chat_id, message_id,
                        "🟢 <b>No build is currently running.</b>",
                        menu_keyboard(),
                    )
            return

        if data.startswith("publish:"):
            _, run_id, run_number = data.split(":", 2)
            existing = release_for_run(run_number)
            if existing:
                release_url = existing.get("html_url", "")
                edit_message(
                    chat_id, message_id,
                    "<b>📦 RELEASE ALREADY PUBLISHED</b>\n\n"
                    f"Run <code>#{esc(run_number)}</code> is already released.",
                    [[{"text": "📦 GitHub Release ↗", "url": release_url}]] if release_url else menu_keyboard(),
                )
                return
            dispatch_release(run_id, run_number)
            edit_message(
                chat_id, message_id,
                "<b>🚀 PUBLISHING RELEASE…</b>\n\n"
                f"Build Run <code>#{esc(run_number)}</code> is being published to GitHub Releases.",
                [[{"text": "🔗 Build Actions ↗", "url": f"https://github.com/{REPO}/actions/runs/{run_id}"}]],
            )
            return

        if data.startswith("skip:"):
            _, run_id, run_number = data.split(":", 2)
            edit_message(
                chat_id, message_id,
                "<b>🧪 ACTIONS-ONLY BUILD</b>\n\n"
                "Release not published. Test/download the artifact from the Actions run.",
                [
                    [{"text": "🧪 Actions Run / Download", "url": f"https://github.com/{REPO}/actions/runs/{run_id}"}],
                    [{"text": "🚀 Publish Later", "callback_data": f"publish:{run_id}:{run_number}"}],
                ],
            )
            return

        if data.startswith("deleteconfirm:"):
            if user_id != DELETE_OWNER_ID:
                answer_callback(query_id, "Only the bot owner can delete builds.", True)
                return
            run_id = data.split(":", 1)[1]
            gh("DELETE", f"/repos/{REPO}/actions/runs/{run_id}")
            TRACKED_RUNS.pop(chat_id, None)
            edit_message(
                chat_id, message_id,
                "🗑️ <b>BUILD DELETED</b>\n\n"
                "The GitHub Actions run and its Actions artifacts have been removed.",
                menu_keyboard(),
            )
            return

        if data.startswith("deletebuild:"):
            if user_id != DELETE_OWNER_ID:
                answer_callback(query_id, "Only the bot owner can delete builds.", True)
                return
            run_id = data.split(":", 1)[1]
            run = get_run(run_id)
            run_number = run.get("run_number", "?")
            edit_message(
                chat_id, message_id,
                "⚠️ <b>DELETE BUILD?</b>\n\n"
                f"Run <code>#{esc(run_number)}</code> and its Actions artifacts will be permanently deleted.\n\n"
                "This does <b>not</b> delete a GitHub Release if you already published one.",
                [
                    [{"text": "🗑️ YES, DELETE", "callback_data": f"deleteconfirm:{run_id}"}],
                    [{"text": "↩️ Keep Build", "callback_data": "status"}],
                ],
            )
            return

        if data.startswith("cancelrun:"):
            run_id = data.split(":", 1)[1]
            gh("POST", f"/repos/{REPO}/actions/runs/{run_id}/cancel")
            TRACKED_RUNS.pop(chat_id, None)
            edit_message(chat_id, message_id, "🛑 <b>Build cancellation requested.</b>", menu_keyboard())
            return

        if data == "cancel":
            edit_message(chat_id, message_id, menu_text(), menu_keyboard())
            return

    except Exception as exc:
        print(f"callback {data!r} failed: {exc}", file=sys.stderr)
        edit_message(chat_id, message_id, f"❌ <b>Action failed</b>\n<code>{esc(exc)}</code>", menu_keyboard())


def schedule_next_worker():
    # workflow_dispatch is one of the events that GITHUB_TOKEN is allowed
    # to trigger, so the worker can hand off to a fresh runner before exit.
    try:
        gh(
            "POST",
            f"/repos/{REPO}/actions/workflows/{urllib.parse.quote(BOT_WORKFLOW, safe='')}/dispatches",
            {"ref": BRANCH},
        )
        print("Queued next Telegram worker.", flush=True)
    except Exception as exc:
        print(f"Could not queue next Telegram worker: {exc}", file=sys.stderr)


def process_updates(updates):
    for update in updates:
        try:
            if "message" in update:
                handle_message(update["message"])
            elif "callback_query" in update:
                handle_callback(update["callback_query"])
        except Exception as exc:
            print(f"update {update.get('update_id', '?')} failed: {exc}", file=sys.stderr)


def main():
    print("Zephyr GitHub Telegram worker started", flush=True)

    try:
        tg("deleteWebhook", {"drop_pending_updates": False})
    except Exception as exc:
        print(f"deleteWebhook warning: {exc}", file=sys.stderr)

    # Long-lived worker: stay online for ~5h40m, safely below GitHub's 6h
    # GitHub-hosted job limit. A later scheduled/manual worker can take over.
    deadline = time.monotonic() + WORKER_SECONDS
    offset = 0

    while time.monotonic() < deadline:
        remaining = max(1, int(deadline - time.monotonic()))
        poll_timeout = min(POLL_TIMEOUT, remaining)

        try:
            updates = tg(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": poll_timeout,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=poll_timeout + 10,
            ) or []
        except Exception as exc:
            print(f"Telegram polling failed: {exc}", file=sys.stderr)
            time.sleep(2)
            continue

        if updates:
            print(f"Processing {len(updates)} Telegram update(s).", flush=True)
            process_updates(updates)
            offset = max(update["update_id"] for update in updates) + 1

        try:
            monitor_builds()
        except Exception as exc:
            print(f"monitor loop failed: {exc}", file=sys.stderr)

    schedule_next_worker()

    try:
        tg(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=5,
        )
    except Exception as exc:
        print(f"final Telegram confirmation failed: {exc}", file=sys.stderr)

    print("Telegram long-polling window finished.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
