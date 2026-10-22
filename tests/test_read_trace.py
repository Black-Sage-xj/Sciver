from __future__ import annotations

import unittest

from theory_master.read_trace import paths_from_event, unread_selection_sources


ENTRY = {
    "branch_canvas": "/tmp/branch.canvas",
    "control_canvas": "/tmp/control.canvas",
    "start_node_id": "h-frontier",
    "parent_ids": ["h-parent"],
    "scientific_model_ids": ["tm-current"],
    "linked_reports": ["/tmp/nodes/frontier.md"],
    "scientific_model": "/tmp/research/scientific_model.json",
}


class ReadTraceTest(unittest.TestCase):
    def test_command_and_file_read_paths_are_collected(self):
        self.assertEqual(
            paths_from_event({"tool": "sed -n 1,80p /tmp/nodes/frontier.md", "path": "/tmp/branch.canvas"}),
            ["/tmp/branch.canvas", "/tmp/nodes/frontier.md"],
        )

    def test_completed_sdk_command_detail_counts_only_opened_files(self):
        event = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                 '/usr/bin/bash -lc "cat \'/tmp/research plan.md\' /tmp/branch.canvas"'}
        self.assertEqual(paths_from_event(event), ["/tmp/research plan.md", "/tmp/branch.canvas"])
        self.assertEqual(paths_from_event({"event": "TOOL_ACTIVITY", "tool": "command",
                                           "detail": "touch /tmp/unread.md"}), [])

    def test_multiple_cat_operands_and_shell_segments_are_all_recorded(self):
        event = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                 "/usr/bin/bash -lc 'cat /tmp/research_plan.md /tmp/scientific_model.json; cat /tmp/control.canvas'"}
        self.assertEqual(paths_from_event(event), [
            "/tmp/research_plan.md", "/tmp/scientific_model.json", "/tmp/control.canvas",
        ])

    def test_python_open_variable_is_recorded_and_glob_literal_is_not(self):
        event = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                 '/usr/bin/bash -lc "python -c \"import json; p=\'/tmp/control.canvas\'; d=json.load(open(p))\""'}
        self.assertEqual(paths_from_event(event), ["/tmp/control.canvas"])
        glob = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                "/usr/bin/bash -lc \"rg needle -g '*.json' /tmp\""}
        self.assertEqual(paths_from_event(glob), [])

    def test_csv_and_dataframe_reads_are_recorded(self):
        shell = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                 "/usr/bin/bash -lc 'head -n 3 /tmp/train.csv'"}
        self.assertEqual(paths_from_event(shell), ["/tmp/train.csv"])
        dataframe = {"event": "TOOL_ACTIVITY", "tool": "command", "detail":
                     "python - <<'PY'\nimport pandas as pd\nfor name in ['train.csv','test.csv']:\n    df=pd.read_csv(base/name)\nPY"}
        self.assertEqual(paths_from_event(dataframe), ["train.csv", "test.csv"])

    def test_cited_unread_node_and_report_are_rejected(self):
        missing = unread_selection_sources(
            "依据 h-frontier 与 frontier.md 选题", [], ENTRY,
        )
        self.assertIn("h-frontier", missing)
        self.assertIn("/tmp/nodes/frontier.md", missing)

    def test_opened_sources_pass(self):
        missing = unread_selection_sources(
            "读了 h-frontier 和 frontier.md",
            ["/tmp/branch.canvas", "/tmp/nodes/frontier.md"],
            ENTRY,
        )
        self.assertEqual(missing, [])

    def test_rationale_without_entry_sources_is_allowed_for_selection_handoff(self):
        missing = unread_selection_sources("这个候选更重要", ["/tmp/branch.canvas"], ENTRY)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
