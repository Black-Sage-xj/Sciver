"""Cycle-provenance Web timeline regressions: no scientific process execution."""
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from theory_master.tui.recent_provenance import cycle_provenance, enrich_recent_events
from tm_observer.run_views import _recent_timeline


class RecentCycleProvenanceTests(unittest.TestCase):
    def _logs(self, root, cycle, events):
        folder = root / str(cycle)
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "runtime_events.jsonl").open("w") as out:
            for event in events:
                out.write(json.dumps(event) + "\n")

    def _start(self, cycle, time, role, stage, invocation):
        return {"ts": time, "event": "AGENT_STARTED", "role": role,
                "stage": stage, "invocation_id": invocation}

    def _done(self, time, role, stage, invocation):
        return {"ts": time, "event": "AGENT_COMPLETED", "role": role,
                "stage": stage, "invocation_id": invocation}

    def test_original_files_overrule_missing_cycle_started_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = "2026-10-13T14:"
            self._logs(root, 83, [
                self._start(83, d+"32:54+08:00", "judge", "terminal_assessment", "j83"),
                self._done(d+"33:22+08:00", "judge", "terminal_assessment", "j83"),
                self._start(83, d+"33:23+08:00", "research_lead", "cycle_synthesis", "l83"),
                self._done(d+"33:58+08:00", "research_lead", "cycle_synthesis", "l83"),
                self._start(83, d+"35:23+08:00", "reporter", "report", "r83"),
                self._done(d+"35:38+08:00", "reporter", "report", "r83"),
                {"ts": d+"35:51+08:00", "event": "JUDGE_VERDICT", "verdict": "open"},
            ])
            self._logs(root, 84, [
                self._start(84, d+"35:53+08:00", "research_lead", "frontier_proposal", "l84"),
                self._done(d+"36:55+08:00", "research_lead", "frontier_proposal", "l84"),
            ])
            entries = [
                ("32:54", "Judge done 00:28", "AGENT_STARTED", "judge"),
                ("33:23", "Research Lead done 00:35", "AGENT_STARTED", "research_lead"),
                ("35:23", "Reporter done 00:15", "AGENT_STARTED", "reporter"),
                ("35:51", "Judge verdict: open", "JUDGE_VERDICT", ""),
                ("35:53", "Research Lead done 01:02", "AGENT_STARTED", "research_lead"),
            ]
            raw = "\n".join("14:"+clock+":00  "+text if len(clock)==5 else "" for clock,text,_,_ in [])
            # No Cycle started strings at all: origin comes from the actual
            # per-Cycle event files, not a guessed previous group.
            lines=[]
            events=[]
            for clock,text,kind,role in entries:
                at=d+clock+"+08:00"
                lines.append(clock.replace(":",":",1))
                events.append({"at":at, "kind":kind, "role":role, "hint":text})
            readable = "\n".join("14:"+clock+"  "+text for clock,text,_,_ in entries)
            provenance = cycle_provenance(root, "84")
            enriched = enrich_recent_events(events, provenance)
            page = BeautifulSoup(_recent_timeline(readable,enriched), "html.parser")
            groups = page.select("details.tm-cycle-group")
            self.assertEqual([g["data-cycle-group-key"] for g in groups],
                             ["cycle-83","cycle-84"])
            self.assertEqual([len(g.select(".tm-log-item")) for g in groups], [4,1])
            self.assertEqual(len(groups[0].select(".tm-cycle-tail-divider")),1)
            self.assertEqual(len(groups[1].select(".tm-cycle-tail-divider")),0)
            label=groups[0].select_one(".tm-cycle-tail-divider")
            self.assertIn("Judge 裁决后 · 同一 Cycle",label.get_text())
            judge=groups[0].select_one(".tm-log-item")
            self.assertIsNone(page.select_one(".tm-agent-time-range"))
            self.assertEqual([x.get_text(strip=True) for x in judge.select("time")],
                             ["14:32:54"])
            self.assertNotIn("–", judge.get_text())
            self.assertNotIn("尚未记录完成", page.get_text())
            # Cycle 84's Research Lead must not remain inside the ended Cycle 83.
            self.assertIn("Research Lead", groups[1].get_text())
            self.assertNotIn("14:35:53", groups[0].get_text())
            link=groups[0].select_one("a.tm-recent-select")
            self.assertIn("kind=AGENT_STARTED",link["href"])
            self.assertIn("at=2026-10-13T14%3A32%3A54%2B08%3A00",link["href"])

    def test_ambiguous_same_identity_fails_closed_and_no_fabricated_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            ident="2026-10-13T14:00:00+08:00"
            self._logs(root, 1,[self._start(1,ident,"judge","terminal_assessment","j1")])
            self._logs(root, 2,[self._start(2,ident,"judge","terminal_assessment","j2")])
            mapping=cycle_provenance(root,2)
            self.assertNotIn((ident,"AGENT_STARTED","judge"),mapping)
            enriched=enrich_recent_events(
                [{"at":ident,"kind":"AGENT_STARTED","role":"judge","hint":""}],mapping)
            self.assertNotIn("cycle_id",enriched[0])
            # A valid but not completed run has no invented finish timestamp.
            self.assertNotIn("completed_at",mapping.get((ident,"AGENT_STARTED","judge"),{}))
            open_row=BeautifulSoup(_recent_timeline(
                "14:00:00  Judge done 00:00",
                [{"at":ident,"kind":"AGENT_STARTED","role":"judge","hint":"Judge done 00:00"}]),
                "html.parser")
            self.assertIsNone(open_row.select_one(".tm-agent-time-range"))
            self.assertNotIn("尚未记录完成", open_row.get_text())
            self.assertEqual(open_row.select_one("time").get_text(strip=True), "14:00:00")

    def test_metadata_priority_over_conflicting_text_and_legacy_resume(self):
        lines=[
            "11:00:00  Cycle started · 7",
            "11:00:01  Judge done 00:02",
            "11:00:02  Cycle started · 7",
            "11:00:03  Research Lead done 00:04",
            "11:00:04  Cycle started · 8",
        ]
        source=[
            {"at":f"2026-10-13T{11:02d}:00:0{i}+08:00",
             "kind":"CYCLE_STARTED" if "Cycle started" in text else "AGENT_STARTED",
             "role":"judge" if "Judge" in text else "research_lead" if "Research Lead" in text else "",
             "hint":text,"cycle_id":"7" if i < 4 else "8"}
            for i,text in enumerate(lines)
        ]
        page=BeautifulSoup(_recent_timeline("\n".join(lines),source),"html.parser")
        groups=page.select("details.tm-cycle-group")
        self.assertEqual([g["data-cycle-group-key"] for g in groups],["cycle-7","cycle-8"])
        self.assertIn("再次进入运行", groups[0].get_text())
        self.assertEqual(len(groups[0].select(".tm-log-item")),3)
        self.assertEqual(len(groups[1].select(".tm-log-item")),0)
        # If explicit source metadata and old textual marker disagree, source wins.
        source[-1]["cycle_id"]="9"
        page=BeautifulSoup(_recent_timeline("\n".join(lines),source),"html.parser")
        self.assertEqual(page.select("details.tm-cycle-group")[-1]["data-cycle-group-key"],
                         "cycle-9")


if __name__=="__main__":
    unittest.main()
