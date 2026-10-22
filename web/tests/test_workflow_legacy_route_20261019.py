"""Regression: old Workflow URLs must reach the current Stage Preview."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

from tm_observer.web import StateCache, ThreadingHTTPServer, make_handler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WorkflowLegacyRouteTest(unittest.TestCase):
    def test_legacy_workflow_redirects_and_current_preview_still_renders(self):
        with tempfile.TemporaryDirectory() as temp:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(StateCache(Path(temp), ttl_s=1)))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                no_redirect = build_opener(NoRedirect)
                for legacy, destination in (
                    ("/workflow", "/workflow-stage-preview/"),
                    ("/workflow/", "/workflow-stage-preview/"),
                    ("/workflow/?view=full", "/workflow-stage-preview/?view=full"),
                ):
                    with self.subTest(legacy=legacy):
                        with self.assertRaises(HTTPError) as error:
                            no_redirect.open(base + legacy, timeout=10)
                        self.assertEqual(error.exception.code, 303)
                        self.assertEqual(error.exception.headers["Location"], destination)
                with urlopen(base + "/workflow-stage-preview/", timeout=10) as response:
                    page = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("workflow-version-form", page)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
