import json
import unittest
from unittest.mock import patch

from automation import WorkflowError
from editorial_audit import audit
from media import image_review_context
from news_research import write_from_research
from publisher_images import stock_markers


class CompactEditorialTests(unittest.TestCase):
    def test_writer_receives_verified_event_without_unverified_selection_material(self):
        research={'event':'Verified event','sources':[{'url':'https://example.org/verified'}]}
        payload={'input':json.dumps({'now':'2026-10-11','author':'mako',
                  'previous_articles':['Old event'], 'news_candidates':['Unverified event'],
                  'reader_insights':{'private_metric':'Not for article'}}),
                 'instructions':'', 'text':{'format':{'schema':{
                     'properties':{'sources':{'items':{'properties':{'url':{}}}}}}}}}
        with patch('news_research.call_api',return_value={'output':[]}) as api:
            write_from_research(payload,research,{'output':[]})
        sent=json.loads(api.call_args.args[0]['input'])
        self.assertEqual(sent['verified_research'],research)
        for field in ('previous_articles','news_candidates','reader_insights'):
            self.assertNotIn(field,sent)

    def test_combined_editor_rejects_duplicate_even_if_facts_are_correct(self):
        response={'status':'completed','output':[{'type':'message','content':[
            {'type':'output_text','text':json.dumps({'approved':False,
             'revisions':['同じ発表の記事を作成済み']})}]}]}
        previous=[{'title':'Same event','lead':'Same facts'}]
        with patch('editorial_audit.call_api',return_value=response) as api:
            with self.assertRaisesRegex(WorkflowError,'同じ発表'):
                audit({'title':'Paraphrased title','body':'Same facts'}, {}, 'fixture',previous)
        self.assertEqual(json.loads(api.call_args.args[0]['input'])['previous'],previous)
        self.assertEqual(api.call_count,1)

    def test_diagram_review_keeps_full_relevant_section_and_excludes_other_news(self):
        marker='【ここに図解②を挿入｜利用条件】'
        relevant='利用には条件があり、提供はまだ予定です。'*200
        article='# Example\n\n## 何が起きた？\n別の説明\n\n## 利用条件\n'+relevant+'\n'+marker+'\n\n## 感想\n別の意見'
        context=image_review_context(article,{'number':2,'anchor':marker})
        self.assertIn(relevant,context)
        self.assertNotIn('別の説明',context)
        self.assertNotIn('別の意見',context)
        self.assertIn('# Example',context)

    def test_stock_command_does_not_treat_prices_or_inline_dollars_as_charts(self):
        text='$Microsoft\n\n$1900\n\n値段は$20です。\n\n$Microsoft\n\n$富士通'
        self.assertEqual(stock_markers(text),['Microsoft','富士通'])
