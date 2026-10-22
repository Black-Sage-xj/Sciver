"""A Worker card must show its pinned login's quota, never the global selector."""
import json

from bs4 import BeautifulSoup

from tm_observer.run_views import _codex_quota_metric_card, run_detail_body


SNAPSHOT = {
    "active_account": "account-1",
    "accounts": [
        {"account_home": "account-1", "ok": True,
         "primary": {"remaining_percent": 90}, "secondary": {"remaining_percent": 87},
         "queried_at": 1792320000},
        {"account_home": "account-2", "ok": True,
         "primary": {"remaining_percent": 0}, "secondary": {"remaining_percent": 84},
         "queried_at": 1792320000},
    ],
}


def test_worker_quota_card_uses_pinned_account_not_global_selector():
    html = _codex_quota_metric_card(SNAPSHOT, worker_account="2")
    doc = BeautifulSoup(html, "html.parser")
    assert doc.select_one("[data-quota-account]").get_text(strip=True) == "Codex 2"
    assert doc.select_one("[data-quota-value]").get_text(strip=True) == "5 小时剩余：0%"
    assert doc.select_one("[data-quota-week]").get_text(strip=True) == "7 天剩余：84%"
    global_html = _codex_quota_metric_card(SNAPSHOT)
    assert "Codex 1" in global_html
    assert "5 小时剩余：90%" in global_html


def test_worker_console_passes_account_pin_to_initial_and_live_quota(tmp_path):
    root = tmp_path/"research"
    (root/".theory-master/workers/worker-1").mkdir(parents=True)
    (root/".theory-master/workers/worker-2").mkdir(parents=True)
    (root/".theory-master/worker_mode.json").write_text(json.dumps({
        "schema":"theory-master/worker-mode/v1","enabled":True,"desired_agents":2
    }))
    for i, account in ((1,"1"),(2,"2")):
        (root/".theory-master/workers"/f"worker-{i}"/"runtime.json").write_text(json.dumps({
            "schema":"theory-master/worker-runtime/v1",
            "worker_id":f"worker-{i}", "current_cycle":str(i),
            "status":"PAUSED", "account":account
        }))
        cycle=root/str(i)
        cycle.mkdir()
        (cycle/".theory-master-cycle").write_text("v2")
        (cycle/"run_state.json").write_text(json.dumps({
            "status":"PAUSED","phase":"provider_quota_exhausted",
            "cycle_index":1,"max_cycles":2
        }))
    run={"research_root":str(root),"observer_id":"a"*16,"cycle_dir":str(root/"2"),
         "cycle":"2","task_name":"Quota test","state":"PAUSED"}
    body=run_detail_body(run,worker_id="worker-2",quota_snapshot=SNAPSHOT)
    assert "const workerQuotaAccount=\"account-2\"" in body
    doc=BeautifulSoup(body,"html.parser")
    assert doc.select_one("[data-quota-account]").get_text(strip=True)=="Codex 2"
    assert "5 小时剩余：0%" in doc.select_one("[data-quota-value]").get_text(strip=True)
