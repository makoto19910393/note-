"""One entry point for scheduled generation and optional note publication."""
import argparse
from datetime import datetime, timedelta
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

from automation import ROOT, WorkflowError, APIError, run


def persist_before_publish():
    """Persist a remote marker before any irreversible click on ephemeral CI."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return  # Local runs already retain state on the same filesystem.
    diff=subprocess.run(['git','diff','--quiet','HEAD','--','content/'],cwd=ROOT,capture_output=True,timeout=30)
    untracked=subprocess.run(['git','ls-files','--others','--exclude-standard','content/'],cwd=ROOT,capture_output=True,timeout=30)
    if diff.returncode not in (0,1) or untracked.returncode:
        raise WorkflowError('公開前のGitHub保存を確認できません。noteの公開操作は行っていません。')
    if diff.returncode==0 and untracked.returncode==0 and not untracked.stdout.strip():
        return
    commands = [
        ["git", "config", "user.name", "note-draft-bot"],
        ["git", "config", "user.email", "note-draft-bot@users.noreply.github.com"],
        ["git", "add", "content/"],
        ["git", "commit", "-m", "note公開前の状態を保存"],
        ["git", "pull", "--rebase", "origin", "main"],
        ["git", "push", "origin", "HEAD:main"],
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


def batch_run_ids(config,state,date,slot):
    count=config.get('articles_per_slot',1)
    if type(count) is not int or not 1<=count<=6:
        raise WorkflowError('時間枠の記事数の設定が不正です。')
    legacy=date+'-'+slot
    existing={a['run_id'] for a in state['articles']}
    if count==1:return [legacy]
    return [legacy if i==1 and legacy in existing else legacy+'-'+str(i)
            for i in range(1,count+1)]


def run_batch(output,config,actual_now,scheduled,slot,mode,checkpoint=None,run_ids=None):
    from publisher import publish
    from media import ensure_images
    checkpoint=checkpoint or persist_before_publish
    state_path=output/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'articles':[]}
    ids=run_ids or batch_run_ids(config,state,scheduled.strftime('%Y-%m-%d'),slot)
    failures=[]
    for i,run_id in enumerate(ids,1):
        try:
            if run_ids is None:
                run(slot,output,{**config,'_batch_index':i},actual_now,
                    run_date=scheduled.strftime('%Y-%m-%d'),run_id=run_id)
                checkpoint()
            current=json.loads(state_path.read_text())
            from publisher import pick_article
            record=pick_article(current,run_id)
            if record.get('note_posted'):
                print('公開済みのため再生成・再投稿しません:',run_id)
                continue
            if record.get('media_plan'):
                ensure_images(output,config,run_id,checkpoint)
            if mode!='disabled':
                publish(output,config,run_id,dry_run=mode=='draft',before_publish=checkpoint)
            checkpoint()
        except APIError:
            checkpoint()
            raise # Quota/auth/ambiguous API errors must not bill the other articles.
        except WorkflowError as error:
            checkpoint()
            failures.append((run_id,str(error)))
            print('この原稿を保留しました:',run_id,str(error))
    if failures:
        raise WorkflowError('一部の記事を保留しました。完了した記事は保存済みです。 '+
                            '; '.join(x[0]+': '+x[1] for x in failures))
    return ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", choices=["auto", "morning", "evening"], default="auto")
    parser.add_argument("--run-id", help="既存原稿の投稿だけを行う。AI APIを再実行しない。")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    actual_now = datetime.now(ZoneInfo(config["timezone"]))
    scheduled, slot = scheduled_slot(config, actual_now, args.slot)
    output = ROOT / "content"
    mode = os.environ.get("NOTE_MODE", "auto")
    if mode == "auto":
        mode = config["publisher"].get("mode", "disabled")
    if mode not in ("disabled", "draft", "publish"):
        raise WorkflowError("NOTE_MODEはdisabled、draft、publishのいずれかを指定してください。")
    if config["publisher"].get("manual_first_publication") and mode == "publish":
        mode = "draft"
        print("ユーザー確認期間のため下書き保存のみ行います。")
    # Check before spending on generation.
    if mode != "disabled" and not (os.environ.get("NOTE_STORAGE_STATE") or
                                    (os.environ.get("NOTE_EMAIL") and os.environ.get("NOTE_PASSWORD"))):
        raise WorkflowError("noteのログイン状態がありません。")
    if mode == "publish" and not (os.environ.get("NOTE_UI_VERIFIED") == "true" or
                                  config["publisher"].get("ui_verified") is True):
        raise WorkflowError("note画面の下書き保存テストが未完了です。NOTE_UI_VERIFIEDを確認してください。")
    if mode=='publish' and config.get('images',{}).get('enabled') and not config['images'].get('ui_verified'):
        raise WorkflowError('画像付き投稿の実画面検証が未完了です。画像なしで公開せず停止します。')
    # Generation evaluates source freshness against actual execution time.
    # The date of the run ID follows the scheduled time even if a job is delayed.
    if args.run_id:
        if mode == "disabled":
            raise WorkflowError("既存原稿の投稿にはdraftまたはpublishを指定してください。")
    if not args.run_id and config.get("reader_analysis", {}).get("enabled"):
        try:
            from research_dashboard import current_reader_insights
            config["_reader_insights"] = current_reader_insights()
            print("読者実績を記事選定に反映します。私的な数値は出力しません。")
        except Exception:
            # Optional analytics failure is reported distinctly; publishing checks remain mandatory.
            config["_reader_insights"] = {"available":False}
            print("今回は読者実績を取得できませんでした。最新ニュースの調査・確認は続けます。")
    run_batch(output,config,actual_now,scheduled,slot,mode,
              run_ids=[args.run_id] if args.run_id else None)


if __name__ == "__main__":
    try:
        main()
    except (WorkflowError, OSError, ValueError, KeyError, ImportError) as e:
        print("自動化を停止しました:", e, file=sys.stderr)
        sys.exit(1)
