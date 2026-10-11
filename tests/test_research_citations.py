import json
from datetime import datetime
import unittest
from unittest.mock import patch

from automation import extract_article, WorkflowError
from news_research import write_from_research, research


class ResearchCitationTests(unittest.TestCase):
    def test_research_cannot_replace_selected_news_with_popular_old_story(self):
        sources=[{'url': 'https://example.org/old-story'},
                 {'url': 'https://example.net/old-story'}]
        wrong={'event':'Another event','facts':[],'unknowns':[],'sources':sources}
        response={'status':'completed','output':[
            {'type':'web_search_call','status':'completed','action':{'sources':sources}},
            {'type':'message','content':[{'type':'output_text','text':json.dumps(wrong)}]}]}
        config={'model':'fixture','_news_candidates':[
            {'title':'Selected new event','url':'https://example.org/new-story'}]}
        with patch('news_research.call_api',return_value=response), self.assertRaisesRegex(
                WorkflowError,'選んだ最新ニュース'):
            research(config,{'articles':[]},datetime.fromisoformat('2026-10-11T19:00:00+09:00'))

    def test_citation_only_evidence_survives_separate_writer(self):
        url = 'https://example.org/verified-story'
        citation = {'type': 'url_citation', 'url': url, 'title': 'Fixture'}
        research = {'output': [
            {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'search'}},
            {'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'Research JSON must not join article JSON.',
                 'annotations': [citation]}]},
        ]}
        article = {'title': 'Fixture article', 'body': 'Fixture body', 'sources': []}
        written = {'status': 'completed', 'output': [
            {'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(article)}]}]}
        payload = {'input': '{}', 'instructions': '', 'text': {'format': {'schema': {
            'properties': {'sources': {'items': {'properties': {'url': {}}}}}}}}}
        with patch('news_research.call_api', return_value=written):
            result = write_from_research(payload, {'sources': [{'url': url}]}, research)
        parsed, evidence = extract_article(result)
        self.assertEqual(parsed, article)
        self.assertIn(url, evidence)

