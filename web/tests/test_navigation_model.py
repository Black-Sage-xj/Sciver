"""Server-derived, safe Web navigation context; no scientific or operator writes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tm_observer.navigation_model import (
    incident_owner_id, task_console_url, task_explorer_url, task_supervisor_url,
)
from tm_observer.supervisor_incident_ui import page_body


class NavigationModelTests(unittest.TestCase):
    def test_canonical_observer_id_links(self):
        ident = 'abcdef0123456789'
        self.assertEqual(task_console_url(ident), f'/run/{ident}/')
        self.assertEqual(task_explorer_url(ident), f'/research-explorer/?observer_id={ident}')
        self.assertEqual(task_supervisor_url(ident), f'/run/{ident}/supervisor')
        for invalid in ('', '1', 'ABCDEF0123456789', '../admin', 'a'*17, 'a'*15 + '/'):
            self.assertIsNone(task_console_url(invalid))
            self.assertIsNone(task_explorer_url(invalid))
            self.assertIsNone(task_supervisor_url(invalid))

    def test_incident_owner_derived_from_verified_research_root(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'research'
            other = Path(folder) / 'other'
            target.mkdir(); other.mkdir()
            runs = [
                {'observer_id': 'abcdef0123456789', 'research_root': str(target)},
                {'observer_id': '1234567890abcdef', 'research_root': str(other)},
            ]
            self.assertEqual(incident_owner_id(runs, target), 'abcdef0123456789')
            self.assertEqual(incident_owner_id(runs, other), '1234567890abcdef')
            self.assertIsNone(incident_owner_id(runs, Path(folder)))
            runs[0]['observer_id'] = 'not-an-id'
            self.assertIsNone(incident_owner_id(runs, target))

    def test_incident_context_links_or_task_list_fallback(self):
        record = {'id': 'SIS-0123456789abcdef', 'status': 'resolved', 'cycle_id': '2'}
        page = page_body(record, [], task_id='abcdef0123456789')
        self.assertIn('href="/run/abcdef0123456789/"', page)
        self.assertIn('href="/run/abcdef0123456789/supervisor"', page)
        self.assertIn('href="/research-explorer/?observer_id=abcdef0123456789"', page)
        fallback = page_body(record, [], task_id='../../bad')
        self.assertIn('href="/run/"', fallback)
        self.assertNotIn('href="../../bad"', fallback)


if __name__ == '__main__':
    unittest.main()
