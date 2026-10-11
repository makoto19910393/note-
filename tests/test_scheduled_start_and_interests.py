from datetime import datetime
import unittest

from news_candidates import prioritize_candidates, candidates_for_region
from run_workflow import generation_has_started


class StartAndInterestTests(unittest.TestCase):
    def test_start_boundary_uses_japan_time_and_does_not_generate_early(self):
        config={'automation_start_at':'2026-10-13T07:00:00+09:00'}
        self.assertFalse(generation_has_started(config,datetime.fromisoformat('2026-10-12T19:00:00+09:00')))
        self.assertFalse(generation_has_started(config,datetime.fromisoformat('2026-10-12T21:59:59+00:00')))
        self.assertTrue(generation_has_started(config,datetime.fromisoformat('2026-10-12T22:00:00+00:00')))

    def test_dashboard_interest_changes_candidate_order_without_losing_fresh_candidates(self):
        candidates=[{'title':'NVIDIA announces a new AI feature'},
                    {'title':'Microsoft Teams adds an AI feature'},
                    {'title':'Microsoft Copilot introduces a different new feature'}]
        insights={'available':True,'high_view_titles':['Microsoft TeamsのAI機能を解説']}
        ranked=prioritize_candidates(candidates,insights)
        self.assertEqual(ranked,[candidates[1],candidates[2],candidates[0]])
        self.assertEqual(len(ranked),len(candidates))
        self.assertEqual(prioritize_candidates(candidates,{'available':False}),candidates)

    def test_japanese_reporting_of_overseas_product_is_not_domestic(self):
        overseas={'title':'OpenAIが新しいAIモデルを発表'}
        domestic={'title':'日本の銀行がGoogle Geminiの導入を発表'}
        unknown={'title':'AIの新しい活用事例を発表'}
        candidates=[overseas,domestic,unknown]
        self.assertEqual(candidates_for_region(candidates,'domestic'),[domestic,unknown])
        self.assertEqual(candidates_for_region(candidates,'overseas'),[overseas,unknown])
