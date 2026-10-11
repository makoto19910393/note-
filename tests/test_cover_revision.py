import unittest

from publisher import should_replace_body


class CoverRevisionTests(unittest.TestCase):
    def setUp(self):
        self.record = {'sha256': 'saved-body', 'draft_source_sha256': 'saved-body',
                       'media_hashes': ['old-cover', 'a', 'b', 'c', 'd']}

    def test_cover_only_update_preserves_phone_reviewed_body(self):
        self.assertFalse(should_replace_body(self.record, 4, ['new-cover', 'a', 'b', 'c', 'd']))

    def test_new_body_is_saved_even_when_diagram_files_are_unchanged(self):
        self.record['sha256'] = 'rewritten-body'
        self.assertTrue(should_replace_body(self.record, 4, self.record['media_hashes']))

    def test_revised_diagrams_replace_existing_body_images(self):
        self.assertTrue(should_replace_body(self.record, 4, ['old-cover', 'new-a', 'b', 'c', 'd']))
