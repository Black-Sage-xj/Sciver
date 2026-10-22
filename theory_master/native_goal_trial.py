"""Single isolated real Investigator Native Goal trial from a retained snapshot.

Never changes the source TheoryMaster task or original 2026-10-10 experiments.
Requires Linux bubblewrap; absence is an error, never an unsandboxed fallback.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def _guard(snapshot: Path, output: Path) -> None:
    readonly = getattr(os, "ST_RDONLY", 1)
    # Linux mount namespace must prohibit writes outside the experimental output.
    if not (os.statvfs("/").f_flag & readonly):
        raise RuntimeError("unsafe native goal trial: host root not read-only")
    if not (os.statvfs(snapshot).f_flag & readonly):
        raise RuntimeError("unsafe native goal trial: scientific snapshot not read-only")
    if os.statvfs(output).f_flag & readonly:
        raise RuntimeError("unsafe native goal trial: output is not writable")
    if not (snapshot / "historical_first_investigator_context.json").is_file():
        raise RuntimeError("native goal trial requires preserved first Investigator Context")


def _prepare_ephemeral_codex_home() -> None:
    """Use the existing Codex authorization only in sandbox-private /tmp."""
    original = Path.home()
    root = Path("/tmp/tm-native-goal-home")
    accounts = root / ".codex-accounts"
    accounts.mkdir(mode=0o700, parents=True)
    src = original / ".codex-accounts"
    selected = (src / "current").resolve().name
    if selected not in {"account-1", "account-2"}:
        raise RuntimeError("valid account selector unavailable")
    for name in ("account-1", "account-2"):
        target = accounts / name
        target.mkdir(mode=0o700)
        auth = src / name / "auth.json"
        if auth.is_file():
            shutil.copyfile(auth, target / "auth.json")
            (target / "auth.json").chmod(0o600)
    (accounts / "current").symlink_to(selected, target_is_directory=True)
    (accounts / ".identity-runtime-v2").touch()
    (root / ".codex").mkdir(mode=0o700)
    from .codex_accounts import _RUNTIME_CONFIG
    runtime = root / ".codex-theory-master"
    runtime.mkdir(mode=0o700)
    (runtime / "config.toml").write_text(_RUNTIME_CONFIG, encoding="utf-8")
    os.environ["HOME"] = str(root)
    os.environ["THEORYMASTER_NETWORK_CHECK"] = "0"
    os.environ["THEORYMASTER_COMPUTE_PYTHON"] = sys.executable
    os.environ["THEORY_MASTER_EMAIL_TERMINAL"] = "0"


def _inside(snapshot: Path, output: Path, goal_layout: str,
            resume_artifacts: Path | None = None) -> int:
    _guard(snapshot, output)
    if resume_artifacts is not None:
        if not (os.statvfs(resume_artifacts).f_flag & getattr(os, "ST_RDONLY", 1)):
            raise RuntimeError("prior scientific artifacts must be read-only in isolated continuation")
        if not (resume_artifacts / "workspace" / "frozen_three_mode_protocol.json").is_file():
            raise RuntimeError("cannot continue without retained frozen scientific protocol")
        if not (resume_artifacts / "goal_events.json").is_file():
            raise RuntimeError("cannot continue without preceding authentic Goal event trace")
    _prepare_ephemeral_codex_home()
    from .backend_config import resolve_routes
    from .codex_backend import CodexBackend
    from .native_goal import investigator_goal, run_native_goal, read_goal_findings
    from openai_codex import Sandbox

    ctx = json.loads((snapshot / "historical_first_investigator_context.json").read_text(encoding="utf-8"))
    scientific = ctx["frozen_objective"]
    if not all(scientific.get(k) for k in ("hypothesis", "scope", "decisive_test")):
        raise ValueError("incomplete historical scientific input")
    workspace = output / "workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    ctx["workspace_access"] = {
        "task_home": str(snapshot / "task"),
        "research_root": str(snapshot),
        "benchmark_root": str(snapshot / "benchmark"),
        "cycle_workspace": str(workspace),
        "cycle_dir": str(output),
        "repo_root": str(snapshot),
    }
    if resume_artifacts is not None:
        import hashlib
        frozen = resume_artifacts / "workspace" / "frozen_three_mode_protocol.json"
        sha256 = hashlib.sha256(frozen.read_bytes()).hexdigest()
        ctx["research_continuation"] = {
            "type": "NEW CODEX THREAD FROM SAVED SCIENTIFIC ARTIFACTS, NOT SAME-THREAD RESUME",
            "prior_workspace_read_only": str(resume_artifacts / "workspace"),
            "prior_precommitted_protocol_read_only": str(frozen),
            "prior_precommitted_protocol_sha256": sha256,
            "constraints": (
                "Continue the exact locked scientific H/test from these existing "
                "saved scientific artifacts. The prior model thread was killed by "
                "the Runner 3600-second external job limit; no terminal findings "
                "or Judge verdict exists. Do not overwrite/re-derive the frozen "
                "predictor, shell selection, uncertainty plan, or admissible inputs "
                "after reading the frozen protocol. This is evidence continuity "
                "only, not the same conversational/model thread. Identify and "
                "actually execute what is still missing: controlled C_pair "
                "qualification, held-out independent network generation and "
                "responses, precommitted contrasts, errors, and full submission. "
                "Prior artifacts are read-only; put NEW results only in the "
                "current writable workspace, citing the original provenance."
            ),
        }
    class KernelProtectedGoalBackend(CodexBackend):
        @staticmethod
        def _thread_sandbox(role: str):
            _guard(snapshot, output)
            return Sandbox.full_access  # ONLY inside outer OS-enforced readonly sandbox
    routes = resolve_routes(profile="test", env={"THEORYMASTER_MODEL_PROVIDER": "codex"})
    route = routes["investigator"]
    backend = KernelProtectedGoalBackend(workspace, routes={"investigator": route},
                                         task_config_path=snapshot / "task" / "theory-master.config.json")
    backend.cycle_id = "48-native-goal-test"
    start = time.time()
    observed: list[dict[str, str]] = []
    # Trust is restricted to the outer kernel-isolated experiment workspace.
    config = Path.home() / ".codex-theory-master" / "config.toml"
    with config.open("a", encoding="utf-8") as f:
        f.write("\n[projects." + json.dumps(str(workspace.resolve())) + "]\ntrust_level = \"trusted\"\n")
    thread_id = backend.ensure_thread("investigator", None, route.model, route.provider)
    thread = backend.threads[thread_id]
    result: dict[str, object] = {
        "thread_id": thread_id, "model":route.model, "provider":route.provider,
        "reasoning_effort":route.reasoning_effort, "frozen_science":scientific,
        "native_goal_used":False, "goal_status":"unstarted", "judge_called":False,
        "original_master1_untouched":True, "started_at":start,
        "resume_artifacts":str(resume_artifacts) if resume_artifacts else None,
        "thread_continuity": "new_thread_artifact_continuation" if resume_artifacts else "fresh_thread",
    }
    def save() -> None:
        (output / "result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save()  # Persist exact thread identifier before the costly context orientation.
    try:
        # An independent orientation Turn transfers fixed task context (not Goal).
        handoff=workspace/".tm-handoff"/"trial-findings.json"
        handoff.parent.mkdir(parents=True,exist_ok=True)
        thread.run("At native science completion, write a small administrative JSON file to "
                   +str(handoff)+
                   ' using {"findings":[{"claim":"complete scientific conclusion"}]}. '
                   "Scientific research and files stay freely organized. "
                   "This is a fixed scientific CONTEXT handoff, not a science report. "
                   "Read the frozen input and authorized workspace only. "
                   "Do not begin the experiment or write Findings yet; acknowledge readiness.\n"
                   +json.dumps(ctx,ensure_ascii=False))
        goal_text=investigator_goal("investigate",ctx,layout=goal_layout)
        if resume_artifacts is not None:
            # The complete provenance and immutable frozen science were provided
            # in the setup Context turn; Goal's native objective has a hard
            # 4000-character protocol bound. Keep this as a short durable cue.
            goal_text += (
                "\nCONTINUATION: use read-only prior science and PRECOMMITTED "
                "protocol from research_continuation context; never refit or "
                "reselect. Finish the missing frozen held-out test."
            )
        if len(goal_text) > 4000:
            raise ValueError(f"native Goal objective exceeds 4000 characters: {len(goal_text)}")
        import hashlib
        result["goal_layout"]=goal_layout
        result["goal_sha256"]=hashlib.sha256(goal_text.encode("utf-8")).hexdigest()
        (output/"goal_objective.txt").write_text(goal_text,encoding="utf-8")
        result["goal_chars"]=len(goal_text)
        result["native_goal_used"]=True
        result["goal_status"] = "active"
        save()
        def on_event(event) -> None:
            method=str(getattr(event,"method",""))
            if method in {"turn/started","turn/completed","item/started","item/completed","thread/goal/updated","thread/goal/cleared"}:
                item=getattr(getattr(event,"payload",None),"item",None)
                observed.append({"method":method,
                                 "item_type":type(getattr(item,"root",item)).__name__})
                if len(observed)%10==0:
                    (output/"goal_events.json").write_text(json.dumps(observed,indent=2)+"\n")
                    (output/"progress.json").write_text(json.dumps({
                        "goal_status":"active", "thread_id":thread_id,
                        "native_turns_started":sum(e["method"]=="turn/started" for e in observed),
                        "events_observed":len(observed), "last_event":method,
                        "goal_layout":goal_layout, "completed":False,
                    },indent=2)+"\n")
        goal=run_native_goal(thread,goal_text,on_event=on_event)
        result.update(goal_status=goal.status,physical_turns=goal.physical_turns,
                      tools_started=goal.tools_started)
        if goal.status=="complete":
            thread._client.thread_goal_clear(thread.id)
            raw=read_goal_findings(goal,handoff)
            (output/"final_findings_raw.json").write_text(raw,encoding="utf-8")
            result["formatting_attempted"]=False
            result["handoff_read"]=True
        else:
            result["formatting_attempted"]=False
            result["handoff_read"]=False
        result["duration_seconds"]=round(time.time()-start,2)
        save()
        (output/"goal_events.json").write_text(json.dumps(observed,indent=2)+"\n")
    except BaseException as exc:
        result.update(goal_status="runtime_error",scientific_completion=False,
                      error=type(exc).__name__+": "+str(exc),
                      duration_seconds=round(time.time()-start,2))
        save()
        raise
    finally:
        if backend.client is not None:
            backend.client.close()
    print(json.dumps({k:v for k,v in result.items() if k not in ("frozen_science",)},ensure_ascii=False),flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description="Single H-0046 Native Goal experiment with OS isolation")
    parser.add_argument("--snapshot",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--inside",action="store_true")
    parser.add_argument("--goal-layout",default="v1",choices=("v1","v2"),
                        help="frozen experimental Goal orchestration version (no clock limits)")
    parser.add_argument("--resume-artifacts",type=Path,default=None,
                        help="previous terminated trial's artifacts, read-only; a NEW Codex thread")
    args=parser.parse_args(argv)
    snap=args.snapshot.expanduser().resolve()
    out=args.output.expanduser().resolve()
    prior=args.resume_artifacts.expanduser().resolve() if args.resume_artifacts else None
    if args.inside:
        return _inside(snap,out,args.goal_layout,prior)
    if not snap.is_dir() or not (snap/"historical_first_investigator_context.json").is_file():
        parser.error("missing frozen historical Investigator Context")
    if prior is not None and (not prior.is_dir() or not (prior/"workspace"/"frozen_three_mode_protocol.json").is_file()):
        parser.error("--resume-artifacts requires a previous trial's frozen protocol")
    if prior == out:
        parser.error("previous artifacts and new writable output must be distinct")
    if out.exists():
        parser.error("output directory already exists; no trial overwrite allowed")
    executable=shutil.which("bwrap")
    if not executable:
        parser.error("bubblewrap mandatory; never run unisolated")
    out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if shutil.disk_usage(out.parent).free < 2*1024**3:
        parser.error("less than 2 GiB free; no test")
    out.mkdir(mode=0o700,parents=False)
    cmd=[executable,"--ro-bind","/","/","--tmpfs","/tmp","--dev-bind","/dev","/dev",
         "--proc","/proc","--bind",str(out),str(out),"--ro-bind",str(snap),str(snap),
         "--die-with-parent","--setenv","PYTHONDONTWRITEBYTECODE","1",
         "--setenv","PYTHONPATH",str(Path(__file__).resolve().parents[1]),
         sys.executable,"-m","theory_master.native_goal_trial","--inside",
         "--snapshot",str(snap),"--output",str(out),
         "--goal-layout",str(args.goal_layout)]
    if prior is not None:
        cmd.extend(["--resume-artifacts",str(prior)])
    # No time-based subprocess deadline: only native token/usage budgets or an
    # operator-issued cancellation may terminate this scientific Goal.
    return subprocess.run(cmd, check=False).returncode


if __name__=="__main__":
    raise SystemExit(main())
