import json
import unittest
from unittest.mock import patch

from automation import extract_article
from news_research import write_from_research


class ResearchCitationTests(unittest.TestCase):
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

