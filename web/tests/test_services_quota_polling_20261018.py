"""Services auto-refresh reads shared quota snapshots, not live provider RPCs."""
from __future__ import annotations

import unittest

from tm_observer.web import codex_usage_body


class ServicesQuotaPollingTests(unittest.TestCase):
    def test_auto_poll_is_cached_and_manual_refresh_is_explicitly_live(self):
        body = codex_usage_body()
        self.assertIn('onclick="loadUsage({force:true})"', body)
        self.assertIn("const endpoint = force ? '/codex-usage.json' : '/codex-usage-cached.json';", body)
        self.assertIn("payload.last_refresh_error", body)
        self.assertIn("历史快照", body)
        self.assertNotIn("已同步最近可用快照", body)
        self.assertIn('scheduleUsagePoll(hasCodexSnapshot ? 60000 : 5000)', body)
        self.assertNotIn('setInterval(loadUsage, 60000)', body)
        # Consuming a reset card still does a live preflight and remains guarded by confirmation.
        self.assertIn("fetch('/codex-usage.json', {cache: 'no-store'})", body)
        self.assertIn('resetConfirmationCount(snapshot)', body)


if __name__ == '__main__':
    unittest.main()
