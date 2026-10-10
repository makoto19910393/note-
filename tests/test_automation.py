import contextlib
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import automation as a
from publisher import pick_article, update_record, publish
from run_workflow import scheduled_slot, persist_before_publish


CONFIG = {"timezone": "Asia/Tokyo", "min_article_chars": 80,
          "max_news_age_hours": 48, "slots": {"morning": "07:00", "evening": "19:00"}}
NOW = datetime(2026, 10, 10, 7, tzinfo=ZoneInfo("Asia/Tokyo"))


def response():
    # Fictional example domains. Test fixtures are never published.
    sources = [
        {"title": "検証用資料1", "url": "https://example.org/news/1", "published_at": "2026-10-09T12:00:00+09:00", "supports": "テスト用事実"},
        {"title": "検証用資料2", "url": "https://example.net/news/1", "published_at": "2026-10-09T13:00:00+09:00", "supports": "テスト用事実"},
    ]
    body = "## 30秒で結論\n- 検証用です。\n## 本文\nこれは実ニュースではありません。\n## 私の正直な感想\n例えばという想定例。\n## まとめ\n検証用。\n## 参考リンク\n" + "\n".join(s["url"] for s in sources)
    article = {"title": "【海外AI解説】検証用の記事", "body": body, "sources": sources}
    return {"status": "completed", "output": [
        {"type": "web_search_call", "status": "completed", "action": {"sources": sources}},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(article)}]}
    ]}


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = {"next_number": 1, "articles": []}

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self, data):
        article, evidence = a.extract_article(data)
        return a.validate_article(article, evidence, CONFIG, self.state, NOW)

    def test_real_pipeline_saves_number_and_skips_second_api_call(self):
        with patch.object(a, "call_api", return_value=response()) as api, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(a, "make_request", return_value={}):
                result = a.run("morning", self.root, CONFIG, NOW)
                self.assertIn("30秒で結論", result.read_text())
                again = a.run("morning", self.root, CONFIG, NOW)
                self.assertEqual(result, again)
                self.assertEqual(api.call_count, 1)
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual(saved["next_number"], 2)
        self.assertFalse(saved["articles"][0]["note_posted"])
        self.assertFalse((self.root / ".automation.lock").exists())

    def test_failed_api_does_not_advance_number_or_leave_lock(self):
        with patch.object(a, "make_request", return_value={}), patch.object(a, "call_api", side_effect=a.WorkflowError("失敗")):
            with self.assertRaises(a.WorkflowError):
                a.run("morning", self.root, CONFIG, NOW)
        self.assertFalse((self.root / "state.json").exists())
        self.assertFalse((self.root / ".automation.lock").exists())

    def test_rejects_unsearched_article(self):
        data = response()
        data["output"].pop(0)
        with self.assertRaises(a.WorkflowError):
            self.validate(data)

    def test_rejects_invented_source(self):
        data = response()
        data["output"][0]["action"]["sources"] = []
        with self.assertRaises(a.WorkflowError):
            self.validate(data)

    def test_rejects_old_news(self):
        data = response()
        article = json.loads(data["output"][1]["content"][0]["text"])
        for s in article["sources"]:
            s["published_at"] = "2026-09-01T00:00:00+09:00"
        data["output"][1]["content"][0]["text"] = json.dumps(article)
        with self.assertRaises(a.WorkflowError):
            self.validate(data)

    def test_rejects_future_news(self):
        data = response()
        article = json.loads(data["output"][1]["content"][0]["text"])
        article["sources"][0]["published_at"] = "2027-01-01T00:00:00+09:00"
        data["output"][1]["content"][0]["text"] = json.dumps(article)
        with self.assertRaises(a.WorkflowError):
            self.validate(data)

    def test_rejects_duplicate_story(self):
        article, _ = a.extract_article(response())
        self.state["articles"] = [{"title": "別タイトル", "sources": [s["url"] for s in article["sources"]]}]
        with self.assertRaises(a.WorkflowError):
            self.validate(response())

    def test_rolls_back_new_draft_if_state_save_fails(self):
        article, _ = a.extract_article(response())
        with patch.object(a, "atomic_json", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                a.save_article(self.root, self.state, self.root / "state.json", CONFIG, NOW, "morning", article, [])
        self.assertFalse(list(self.root.rglob("*.md")))

    def test_changed_draft_blocks_retry(self):
        with patch.object(a, "call_api", return_value=response()), patch.object(a, "make_request", return_value={}), contextlib.redirect_stdout(io.StringIO()):
            result = a.run("morning", self.root, CONFIG, NOW)
            result.write_text("手動変更")
            with self.assertRaises(a.WorkflowError):
                a.run("morning", self.root, CONFIG, NOW)

    def test_slots_use_japan_time(self):
        self.assertEqual(scheduled_slot(CONFIG, NOW)[1], "morning")
        evening = NOW.replace(hour=19)
        self.assertEqual(scheduled_slot(CONFIG, evening)[1], "evening")
        previous_evening, slot = scheduled_slot(CONFIG, NOW.replace(hour=1))
        self.assertEqual(slot, "evening")
        self.assertEqual(previous_evening.day, 9)

    def test_ambiguous_publication_is_persisted(self):
        record = {"run_id": "2026-10-10-morning", "status": "publishing"}
        state = {"articles": [record]}
        path = self.root / "state.json"
        update_record(path, state, record, status="publication_uncertain")
        saved = json.loads(path.read_text())
        self.assertEqual(pick_article(saved, record["run_id"])["status"], "publication_uncertain")

    def test_ambiguous_publication_cannot_be_retried(self):
        record = {"run_id": "2026-10-10-morning", "status": "publication_uncertain", "note_posted": False}
        a.atomic_json(self.root / "state.json", {"articles": [record]})
        with self.assertRaisesRegex(a.WorkflowError, "再投稿"):
            publish(self.root, CONFIG, record["run_id"])

    def test_already_published_does_not_need_login_or_browser(self):
        record = {"run_id": "2026-10-10-morning", "status": "published", "note_posted": True}
        a.atomic_json(self.root / "state.json", {"articles": [record]})
        with contextlib.redirect_stdout(io.StringIO()):
            publish(self.root, CONFIG, record["run_id"])

    def test_remote_save_failure_stops_before_publication(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}), patch("run_workflow.subprocess.run") as command:
            command.return_value.returncode = 1
            with self.assertRaisesRegex(a.WorkflowError, "公開操作は行っていません"):
                persist_before_publish()
            self.assertEqual(command.call_count, 1)

    def test_delayed_job_retains_slot_date_but_checks_actual_freshness(self):
        with patch.object(a, "call_api", return_value=response()), patch.object(a, "make_request", return_value={}), contextlib.redirect_stdout(io.StringIO()):
            result = a.run("evening", self.root, CONFIG, NOW, run_date="2026-10-09")
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual(saved["articles"][0]["run_id"], "2026-10-09-evening")
        self.assertEqual(result.parent.name, "2026-10-09")


if __name__ == "__main__":
    unittest.main()
