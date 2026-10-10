"""One entry point for scheduled generation and optional note publication."""
import argparse
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

from automation import ROOT, WorkflowError, run


def persist_before_publish():
    """Persist a remote marker before any irreversible click on ephemeral CI."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return  # Local runs already retain state on the same filesystem.
    commands = [
        ["git", "config", "user.name", "note-draft-bot"],
        ["git", "config", "user.email", "note-draft-bot@users.noreply.github.com"],
        ["git", "add", "content/"],
        ["git", "commit", "-m", "note公開前の状態を保存"],
        ["git", "push"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=120)
        if result.returncode:
            raise WorkflowError("公開前のGitHub保存に失敗しました。noteの公開操作は行っていません。")


def scheduled_slot(config, now, requested="auto"):
    if requested != "auto":
        return now, requested
    options = []
    for name, clock in config["slots"].items():
        hour, minute = map(int, clock.split(":"))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled > now:
            scheduled -= timedelta(days=1)
        options.append((scheduled, name))
    return max(options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=["auto", "morning", "evening"], default="auto")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    actual_now = datetime.now(ZoneInfo(config["timezone"]))
    scheduled, slot = scheduled_slot(config, actual_now, args.slot)
    output = ROOT / "content"
    mode = os.environ.get("NOTE_MODE", "disabled")
    if mode not in ("disabled", "draft", "publish"):
        raise WorkflowError("NOTE_MODEはdisabled、draft、publishのいずれかを指定してください。")
    # Check before spending on generation.
    if mode != "disabled" and not (os.environ.get("NOTE_STORAGE_STATE") or
                                    (os.environ.get("NOTE_EMAIL") and os.environ.get("NOTE_PASSWORD"))):
        raise WorkflowError("noteのログイン状態がありません。")
    if mode == "publish" and os.environ.get("NOTE_UI_VERIFIED") != "true":
        raise WorkflowError("note画面の下書き保存テストが未完了です。NOTE_UI_VERIFIEDを確認してください。")
    # Generation evaluates source freshness against actual execution time.
    # The date of the run ID follows the scheduled time even if a job is delayed.
    run(slot, output, config, actual_now, run_date=scheduled.strftime("%Y-%m-%d"))
    if mode != "disabled":
        from publisher import publish
        run_id = scheduled.strftime("%Y-%m-%d") + "-" + slot
        publish(output, config, run_id, dry_run=mode == "draft", before_publish=persist_before_publish)


if __name__ == "__main__":
    try:
        main()
    except (WorkflowError, OSError, ValueError, KeyError, ImportError) as e:
        print("自動化を停止しました:", e, file=sys.stderr)
        sys.exit(1)
