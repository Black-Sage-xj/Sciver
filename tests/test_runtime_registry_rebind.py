import json
import tempfile
import unittest
from pathlib import Path
from theory_master.runtime_manager import registry_path, update_registry

class RuntimeRegistryRebindTest(unittest.TestCase):
    def test_stale_absolute_research_root_follows_relocated_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / "new" / "research"
            file = registry_path(runtime)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps({
                "schema":"theory-master/runtime/v1",
                "research_root":"/old/vault/.theory-master/research",
                "state":"STOPPED",
                "current_cycle":"18",
            }))
            out = update_registry(runtime)
            self.assertEqual(out["research_root"], str(runtime.resolve()))
            self.assertEqual(out["current_cycle"], "18")
            self.assertEqual(json.loads(file.read_text())["research_root"], str(runtime.resolve()))
