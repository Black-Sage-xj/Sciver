from __future__ import annotations

"""Authoritative TheoryMaster stage metadata and routing."""

from typing import Any, Mapping

ROLE_STAGE_LABELS = {
    "frontier_proposal": "propose the next scientific frontier candidates",
    "hypothesis_display": "faithfully compress the frozen Hypothesis claim and conditions for display",
    "candidate_selection": "select and lock one qualified candidate",
    "selection_repair": "repair an invalid candidate selection without reopening the scientific process",
    "cycle_synthesis": "synthesize the terminal Judge decision and conservatively maintain the long-term Scientific Model",
    "frontier_maintenance": "maintain existing Hypothesis frontier states independently of graph leaf status",
    "candidate_review": "independently review the current candidate shortlist",
    "repair_verification": "verify once that the original Challenger scientific blocker was repaired",
    "investigate": "advance the frozen scientific question using whatever internal theory/numerical/local-source methods are needed",
    "preserve_research": "preserve existing science (A), then compress its display (B); SDK projects and closes (C) without downstream Agents",
    "integrate_literature": "integrate newly verified external scholarly evidence into the ongoing Investigation",
    "judge_rework": "perform the one authorized Judge rework",
    "landscape": "map an external scholarly field around the current research frontier",
    "targeted_source": "acquire and verify one targeted external scholarly source/claim",
    "novelty_check": "position the current result or hypothesis against external prior work",
    "terminal_assessment": "evaluate the compact Investigation Result against the frozen Cycle criteria",
    "goal_completion": "decide whether the completed research trajectory satisfies the task Final Gate",
    "report": "write the post-Judge branch report",
}

STAGE_CARDS: dict[tuple[str, str], dict[str, str]] = {
    ("research_lead", "frontier_proposal"): {
        "objective": "Identify the most consequential unresolved scientific gap advancing an unmet Goal, Research Contract, Final Gate or necessary prerequisite. Scope one meaningful scientific claim: avoid umbrella programs and fragments that add no scientific information. Prioritize expected information and Goal relevance, not preparation. A useful counterexample, bounded mechanism, or theory boundary can advance understanding without completing the Goal. Do not create filler or preparation-only candidates.",
        "may_do": "Use the injected Goal/Contract/Final Gate, Scientific Model, adjudicated Outcomes and SQLite graph as scientific authority; feedback, Literature and ResearchFocus are context, not Judge Evidence. Frontier labels are opportunity metadata, not graph leaves or verdicts; descendants do not disqualify parents. Prefer relevant active hypotheses; justify historical pivots by Goal impact. Closed/parked history may inform a new proposition without being reopened. Each Cycle should pursue a scientifically distinct, independently adjudicable claim; wording changes alone do not justify a new claim or repeat a running claim/test. Explain why the gap matters now versus plausible alternatives and how the result would change the scientific picture. Tests must add discriminating information beyond adjudicated outcomes; pure confidence strengthening is insufficient unless a new source of uncertainty exists. A matching, contrary, or inconclusive result needs a meaningful interpretation. A matching local result may advance the Cycle without supporting the whole hypothesis; a contrary result refutes H only when its local prediction is necessary. Use relevant alternatives or limiting cases, not irrelevant baselines. Match uncertainty and proof precision to the claimed inference; do not impose universal thresholds. Indispensable premises need a credible in-scope route; proofs, nontrivial calculations and uncertain scientific prerequisites remain Investigator work. Do not hide an independent theorem, data qualification or separate scientific proposition inside one test; keep each Cycle to one coherent adjudicable claim. Local, negative, high-risk or otherwise credible work remains eligible without completing the Final Gate. Use Literature only when a blocking external scholarly gap prevents responsible formulation.",
        "must_not": "Do not author Canvas display title or summary or geometry; infer frontier state from graph degree; or treat an active label as a command for the next Cycle. Do not select before Challenger, perform Investigator research, or alter frontier state. Do not reopen a closed hypothesis or continue a parked one unchanged; same-claim retesting needs a scientific revision or new uncertainty. Use external novelty/priority review only when it blocks the Goal or Final Gate.",
        "exit": "Complete when genuine candidate options address the consequential gap, or a blocking external scholarly gap makes a Literature request the only responsible route. The SDK owns candidate schema, selection/locking, lineage persistence and display-copy request.",
    },
    ("research_lead", "hypothesis_display"): {
        "objective": "After Hypothesis Lock, faithfully compress the selected and frozen Hypothesis for graph display; this is presentation only, not another scientific decision.",
        "may_do": "Read only the injected frozen_hypothesis fields (hypothesis, scope, decisive_test). Return a short title, the concrete claim to explain or predict, and the indispensable applicability conditions as separate fields. The SDK deterministically composes the summary and owns the epistemic label, initially 待检验.",
        "must_not": "Do not revise or reinterpret the frozen hypothesis, scope, decisive test, identity, lineage, evidence, or verdict. Do not open files, use post-lock findings, imply support, or return a free-form canvas_summary. If faithful compression is not possible, let the SDK use the bounded safe fallback; never block Investigation.",
        "exit": "Return only canvas_title (<=48 chars), canvas_claim (<=78 chars), and canvas_conditions (<=54 chars). SDK validates and composes the <=160-character summary.",
    },
    ("research_lead", "candidate_selection"): {
        "objective": "Choose one candidate only from the already-qualified candidate set and explain the preference briefly.",
        "may_do": "Compare qualified candidates for scientific value, discriminating power, Goal advancement, executability, information gain and proportionate cost. The candidate's decisive_test is frozen and remains the sole Cycle test definition.",
        "must_not": "Do not re-review qualification, choose revise/reject candidates, regenerate the frontier, rewrite the hypothesis/scope/decisive_test, create criteria, or route specialist roles.",
        "exit": "Return exactly one selected qualified candidate and a concise selection reason; SDK performs Hypothesis Lock.",
    },
    ("research_lead", "selection_repair"): {
        "objective": "Repair one selected revise candidate against its frozen Challenger blocker ledger without reopening frontier search.",
        "may_do": "Resolve each supplied blocker once using exact blocker IDs and only the minimum clarification or patch preserving the frozen scientific target, scope and estimand. On resolved, return the COMPLETE revised decisive_test containing all binding clarifications, outcome criteria and operational definitions; blocker_closures are audit explanations, never a substitute for the final frozen scientific objective.",
        "must_not": "Do not replace the parent candidate, propose a new direction, change scientific scope/estimand, or solve Investigator-owned research. If a blocker cannot be closed inside the frozen boundary, return cannot_repair; cannot_repair does not lock H and does not enter Investigator.",
        "exit": "Return a blocker-complete repair or cannot_repair with a reason. cannot_repair remains unqualified and does not enter Investigator. SDK retains the parent and keeps an unqualified repair from entering Investigation.",
    },
    ("research_lead", "cycle_synthesis"): {
        "objective": "Synthesize the terminal Judge decision into the compact Scientific Model across relevant Cycles and distinguish established conclusions from unresolved questions.",
        "may_do": "Use the active locked Hypothesis, Judge verdict/rationale, existing Scientific Model and relevant SQLite-backed prior outcomes. Preserve older still-valid premises, scope boundaries, conflicts and uncertainty across relevant Cycles; rewrite the model as one concise coherent paragraph only when durable scientific understanding changes. The 2400-character validator limit is a ceiling, never a target. Never strengthen a claim beyond its adjudicated evidence.",
        "must_not": "Do not override Judge, change the Goal/Contract/Final Gate, create next-Cycle candidates, maintain frontier state in the model, write a Cycle chronology, or promote a local result beyond its adjudicated scope.",
        "exit": "Return validated scientific_synthesis and a keep/replace decision for the Scientific Model; SDK owns persistence and cross-Cycle projection.",
    },
    ("research_lead", "frontier_maintenance"): {
        "objective": "After this Cycle, maintain H frontier_status independent of graph leaves, graph leaf topology, or Outcome verdict. This manages research opportunities, not a Canvas pruning operation. It does not decide what to investigate next. frontier_maintenance and frontier_proposal are different stages: this stage only labels existing H; frontier_proposal is the later stage that proposes Candidates.",
        "may_do": "Read Goal, Contract, Final Gate, Scientific Model, Judge and synthesis; inspect relevant H, their Cycle Outcomes and SQLite frontier states rather than looking for graph tips. For each relevant H give active/parked/closed with reason grounded in remaining Goal opportunities, regardless of derived children or a local supported/rejected Cycle Outcome. SDK persists state without clearing non-leaf labels. Exclude External nodes and external_reference edges.",
        "must_not": "Do not propose a Candidate, select the next Cycle or create a new H; this stage manages frontier state, not next-Cycle selection. Do not modify scientific_status, Judge verdict, Scientific Model, Canvas file, or infer Task COMPLETE merely because every frontier is closed.",
        "exit": "Return only updates. Each item is hypothesis_id, status=closed|parked|active, and reason. No other fields.",
    },
    ("challenger", "candidate_review"): {
        "objective": "Independently assess Goal alignment and test discrimination for an unmet Goal/Final Gate requirement or necessary prerequisite. Qualify the design, not the truth of the hypothesis. Bounded mechanisms, counterexamples and negative results can be valuable without immediate Goal completion.",
        "may_do": "Judge hypothesis–test alignment, inferential capacity and scope. Assess falsifiability, identity and information beyond already terminal-adjudicated tests; adjudicability alone is not sufficient for qualification. Matching, contrary and genuinely inconclusive results must be meaningful at the claimed scope; a necessary local prediction is acceptable without settling the whole hypothesis when informative. Numeric thresholds need a theoretical, prior or design basis; otherwise require effect estimation with uncertainty instead of an invented cutoff. Judge uncertainty and independent-unit sample size against the scientific distinction claimed, not merely whether calculations run. Separate cheap metadata checks from Investigator-owned science: indispensable data/object/method preconditions need a credible in-scope route, and any required comparison set must be feasible and non-empty. An unresolved scientific prerequisite is not a qualification blocker merely because it has not yet been derived or operationalized: with a credible in-scope route, treat it as Investigation work rather than a revise blocker. Omitted cheap input checks remain qualification gaps. Do not convert an Investigator-owned prerequisite into a design blocker unless the essential route is demonstrably unavailable or the decisive outcome is unreachable. Identify every visible independent blocker. Flag leakage, hidden assumptions and bundled independent claims.",
        "must_not": "Do not treat unfinished derivation, uncertain effect, cost, or an eventual open/negative result as a design failure when the test remains executable and discriminating. Do not demand optional stronger claims, irrelevant baselines, arbitrary cutoffs or extra robustness. Do not adjudicate scientific truth, perform Investigator research, change the candidate or select the winner.",
        "exit": "Qualify when the design advances an unmet Goal with no material blocker and a credible in-scope route. Revise only when every blocker is repairable inside the unchanged hypothesis/scope/estimand. Reject only when Goal-unrelated, repeating a resolved question without new uncertainty, requiring a changed scientific target, or lacking a credible essential route. Reject the design, not the broader research direction; topical overlap alone is not a reason.",
    },
    ("challenger", "repair_verification"): {
        "objective": "Perform exactly one post-Lead-revision scientific qualification of the SDK-closed repaired Candidate. Check the original frozen blockers and a fixed, finite set of necessary conditions for the revised decisive_test: operational definitions/inputs, a feasible in-scope route, a non-empty required comparison set, meaningful matching/contrary/inconclusive interpretations, and no newly exposed fatal design flaw.",
        "may_do": "Compare only the original Candidate, frozen blocker ledger, SDK-closed repaired Candidate, Goal/Contract/Final Gate, supplied SQLite H/Outcome context and existing task assets as needed. Inspect a necessary data/comparison precondition as a bounded feasibility check; if exact matching or pairing is required, verify that eligible comparison units can exist. Confirm only if every original blocker is closed, the fixed qualification conditions pass, and Hypothesis, Scope, Estimand and Goal Advance remain unchanged.",
        "must_not": "Do not invent optional criteria, arbitrary thresholds, extra robustness or stronger claims; do not reject an in-scope prerequisite merely because Investigator has not yet derived or operationalized it. A weak/uncertain effect or eventual open result is an empirical outcome, not a design failure when the test remains executable and discriminating. Do not execute the full decisive test, conduct Investigator research, reopen candidate_review, add another review, rank Candidates, demand Investigator work, suggest further repair, change the Candidate or select an H.",
        "exit": "Return exactly candidate_id, verdict=confirmed|unresolved and reason. Use unresolved only when an original blocker remains or a fixed-checklist fatal flaw makes the revised test infeasible, leaves its required comparison set empty, destroys identifiability, or prevents meaningful scientific discrimination; identify the concrete issue. Confirmed means eligible for Investigator, not a prediction of scientific success. No repair instructions; this Stage runs once.",
    },
    ("investigator", "preserve_research"): {
        "objective": "After Supervisor or operator authorization, preserve already obtained unadjudicated scientific work in ONE Investigator Stage; do not execute the frozen decisive_test.",
        "may_do": "First A / Scientific: read existing Cycle artifacts and return evidence-qualified provisional observations with source paths, bounded failed-method limitations, remaining prerequisite, and exactly which parts of decisive_test remain unexecuted. Then B / Presentation in the SAME response: compress only that established A content into one short Canvas title and scope-qualified unadjudicated summary. Do not add a scientific claim or new evidence during compression.",
        "must_not": "Do not conduct new experiments, re-open the science, revise frozen H/scope/test, infer any Judge verdict, claim blind validation from exposed outcomes, invent evidence or decide to stop the Hypothesis. Do not call or request Reporter, Judge, Literature, Lead, or a second presentation model turn. SDK alone owns source checks, MD rendering, Canvas links/frontier_status and Cycle OVER closure.",
        "exit": "One response containing A: observations, methods_exhausted, remaining_gap, unexecuted_test; and B: canvas_title (<=48 chars), canvas_summary (<=160 chars). SDK validates and performs C without further Agent calls. The next Cycle Lead, when independently started, may consult the preserved source but is not directed by this Stage.",
    },
    ("investigator", "investigate"): {
        "objective": "Complete the full frozen decisive research, not merely one or more valid Findings.",
        "may_do": "Privately decompose decisive_test into every necessary scientific prerequisite and its final discriminating comparison. Execute the dependencies in order: qualify definitions and inputs, derive or implement predictions, perform the required independent test and uncertainty checks, then synthesize the outcome. Re-plan when a prerequisite fails; pursue valid in-scope alternatives. Estimate value and resources before costly work; abandon infeasible METHODS; try bounded in-scope alternatives. Deferred qualification blockers are Investigator work within the unchanged frozen hypothesis, scope, estimand, allowed inputs and test. Lead need not prescribe technical steps.",
        "must_not": "Do not hand off after a partial theorem, diagnostic computation, source discovery, invalid sample, or any other technically valid Finding while the decisive test is still executable but unfinished. Do not represent canaries or historical data as fresh independent evaluation, or mistake failure so far for proven impossibility. Optional robustness beyond the frozen test is not required. Do not scale a worst-case bound into unbounded compute or alter the frozen scientific estimand.",
        "exit": "Return complete findings[] ONLY when their combined evidence resolves the frozen decisive test sufficiently for Judge to independently assess the WHOLE Hypothesis: support, falsification, or a genuinely established non-identifiability or inconclusive result after the frozen comparison. If required work remains, KEEP RESEARCHING rather than disguising unfinished work as a Finding qualification. State material conditions and limits in each scientific statement. Do not emit workflow routing fields. Never request native blocked for scientific difficulty; document remaining gaps and continue authorized investigation without fabricating completion.",
    },
    ("investigator", "integrate_literature"): {
        "objective": "Use verified Literature Evidence to finish the same frozen decisive research.",
        "may_do": "Check source provenance and applicability, then perform any newly executable definitions, derivations, numerical computations or decisive comparisons under the unchanged frozen objective.",
        "must_not": "Do not promote an unverified source to scientific fact, restart completed research, or treat a literature summary as resolution of a separate outstanding numerical prerequisite.",
        "exit": "Literature integration is an internal research step, NOT a submission milestone. Continue all necessary research; formally submit complete findings[] only after their combined evidence resolves the frozen decisive test, with honest limits on established results.",
    },
    ("investigator", "judge_rework"): {
        "objective": "Deliver the actual requested_resolution of each authorized Judge objection, not merely another progress report.",
        "may_do": "Treat judge_objections[].requested_resolution as the binding scientific target of this rework. Privately break it into missing prerequisites, execute the necessary theoretical, numerical and source work in order, and verify that the final scientific result answers the exact Judge request under the unchanged frozen objective. If previous data or methods are ineligible, obtain a valid in-scope alternative when feasible. Before costly work, estimate feasibility; abandon a disproportionate method, not the Judge's requested_resolution. If a route fails, document the gap and continue authorized investigation without requesting native blocked. Continue from the retained Investigator thread and prior evidence.",
        "must_not": "A valid Finding or mention of the objection in its statement does not by itself resolve requested_resolution. Do not substitute a weaker surrogate test, replay old Evidence as new, invent independent held-out evaluation, change H/scope/estimand/allowed inputs/frozen test, or delegate the requested resolution back to Judge. Rigorously demonstrate impossibility under frozen conditions rather than assume it.",
        "exit": "BEFORE submitting findings[], ensure their combined science materially RESOLVES the actual requested_resolution: complete its decisive comparison, or establish a rigorous falsification/non-identifiability/impossibility under the frozen conditions that scientifically settles the objection. If the actionable Judge objection remains, KEEP WORKING in this thread: partial Findings are not a completed rework. Return full findings[] only after resolution. Qualifications inside established scientific statements never replace the missing decisive test.",
    },
    ("literature", "landscape"): {
        "objective": "Map the external scholarly field the current research is entering, as structured understanding of what has been done and how, not a paper pile.",
        "may_do": "Use metadata/abstract-level screening for breadth to identify terminology, major technical routes, seminal/representative papers, method evolution, open problems, the relation to the current research, and a reading order; verify at claim level only the few sources that matter to the current research decision. Read the research-lit skill when the map needs structured multi-source synthesis.",
        "must_not": "Do not full-read every retrieved paper, equate citation count with scientific importance, or turn the survey itself into the scientific verdict.",
        "exit": "A compact external field map exists, representative papers are traceable, key uncertainties are explicit, and any claim-level Evidence is clearly separated from metadata-level coverage.",
    },
    ("literature", "targeted_source"): {
        "objective": "Verify the specific external source or claim (definition, theorem, counterexample, method origin, or attribution) that the current research gap requires.",
        "may_do": "When source_lock is supplied, treat it as the frozen source identity. Trace the claim to its original source, read the materialized source text, and verify the locator and exact quote; read the tm-scholar skill for the citation-tracing and verification method.",
        "must_not": "Do not restart a broad topic review, or keep searching once the source question is answered or is unverifiable without a new search lead.",
        "exit": "The external claim/source is verified/partial/conflict/unverified and handed back with provenance and limitations.",
    },
    ("literature", "novelty_check"): {
        "objective": "Position the current TheoryMaster result/hypothesis against external prior work and novelty/priority risk.",
        "may_do": "Identify the closest prior work, compare overlap and genuine differences, identify priority risks and required citations, and distinguish apparent novelty from unverified absence of evidence. Read the novelty-check skill for the claim-decomposition and comparison method.",
        "must_not": "Do not claim exhaustive novelty, hide conflicting prior work to make the result look new, or give a continue/abandon verdict on the research.",
        "exit": "The closest external neighbors and uncertainty of the novelty assessment are explicit enough for Research Lead/Judge to use.",
    },
    ("judge", "terminal_assessment"): {
        "objective": "Independently adjudicate the exact locked Hypothesis through its frozen decisive test, using only the supplied compact scientific result and relevant already-adjudicated predecessor evidence.",
        "may_do": "Assess terminality only for the exact locked Hypothesis and its frozen scope, not for whether the overall research topic still has future questions. First decide whether the current-Cycle result satisfies the frozen decisive test, including any evidence-identity requirement it states (new, independent, or otherwise distinct Evidence); prior adjudicated results may support cumulative reasoning but satisfy such a requirement only when the frozen test permits reuse. Then decide what the result establishes about the locked Hypothesis. Treat each Finding as a self-contained qualified scientific statement; preserve its stated applicability and uncertainty, plus independently validated provenance. Use milestone_advanced only when the current decisive_test is resolved but a requirement logically required by the current locked Hypothesis itself remains unresolved. Request rework whenever a necessary, discriminating result of the frozen decisive_test has not yet been obtained and executable work remains within the unchanged hypothesis/scope. Return a single objection naming the missing SCIENTIFIC DECISION RESULT, even if Investigator must perform several linked prerequisites, simulations or independent network comparisons to obtain it; do not limit rework to a single trivial sub-step. State only requested_resolution and whether external scholarship is needed, leaving method choice to Investigator. A terminal open is appropriate when the frozen test has actually been executed within its evidence/independence/error constraints but remains scientifically inconclusive; do not use terminal open merely because Investigator submitted a partial finding, did not execute the frozen test, or encountered an operational/resource/eligibility obstacle. SDK handles blocked or exhausted non-terminal rework as a resumable execution state, not a scientific verdict.",
        "must_not": "Do not reopen or request raw files, scripts, proofs, Evidence Manifest, Coverage Matrix, Judge Packet, artifact paths or hashes, and do not perform a second investigation of current or prior Cycles. Do not emit criterion IDs, duplicated reasons, scientific-gap taxonomies, search hints, per-Evidence assessments, Evidence IDs, executor/budget/routing details, or a rework plan. Do not relabel historical re-analysis as newly generated or independent Evidence. Do not promote a positive local result into whole-hypothesis support or reject the whole hypothesis from a merely inconclusive local result. Do not block closure or justify milestone_advanced for stronger hypotheses, broader scope, or optional scientific strengthening (extra robustness, higher confidence, future propositions), implementation preferences, or operational/resource concerns unless the locked Hypothesis and frozen scope logically require them.",
        "exit": "Return one verdict with a rationale grounded in the supplied result: either a terminal verdict, or verdict=null with a single rework objection.",
    },
    ("judge", "goal_completion"): {
        "objective": "Independently decide whether the task-level Goal Contract's Final Gate is satisfied for the whole Task. The Goal Contract is not a separate SDK object: it is the Goal + Research Contract + Final Gate + Required Context already written in research_plan.md. This stage asks only whether that Final Gate is met.",
        "may_do": "Read the complete research_plan.md through research_plan_access and identify the completion-critical scientific quantities, predictions, transformations, deliverables, validation conditions, and task-material boundaries in Goal / Research Contract / Final Gate / Required Context. Use agent_graph as the authoritative H/Cycle Outcome trajectory, explicit frontier states, typed lineage and detail_ref links; do not read or parse exploration.canvas. If SQLite context is missing, stop and surface a context error. A supported H or Cycle is never sufficient by itself to prove Goal completion. Follow only the direct local detail links needed to verify a concrete deliverable against the Final Gate. Return passed only when the Final Gate is satisfied; otherwise return not_passed. Explain why without naming a next action.",
        "must_not": "Do not substitute a successful local hypothesis, surrogate metric, or held-out comparison for an original Goal deliverable. Do not invent requirements beyond research_plan.md. Do not treat optional robustness, external generalization, generic future science, or work outside the stated Goal Contract as blockers. Do not propose the next Candidate, change the Scientific Model, alter prior Judge verdicts, redo the Investigation, or perform broad repository exploration. Do not produce a next-step plan. Do not add requirement ledgers, routing, executor, budget, Evidence IDs, artifact inventories, or a research plan to the output.",
        "exit": "Return only verdict=passed|not_passed and one concise rationale explaining why the existing trajectory does or does not satisfy the Final Gate. not_passed does not request a next step.",
    },
    ("reporter", "report"): {
        "objective": "Write a human-first post-Judge scientific narrative from already-decided state.",
        "may_do": "Explain the scientific question, key Evidence, interpretation, counter-explanations, limitations, and model-level meaning in clear research prose (a research note, not a log export) from the already-decided branch and committed Scientific Model state; read the report-writing skill for narrative structure, title, formula, and figure conventions. Preserve Evidence identity as supplied by the SDK: distinguish the origin of the underlying scientific asset from the Cycle that performed the current analysis, and faithfully represent whether the Evidence is current-Cycle material or historical re-analysis. Cite notes with vault-relative wikilinks, repository artifacts with repository-relative paths, and Cycle artifacts relative to cycle_dir.",
        "must_not": "Do not dump JSON/YAML, schemas, contracts, Agent deliveries, raw ModelPatch/evolution-candidate objects, runtime state, or internal field names (e.g. learned/invalidated as headings) into the human report. Do not assess overall Goal completion. A new analysis, derived result, or Evidence record does not make reused data, experiments, simulations, samples, or other scientific assets newly generated or independent; never strengthen historical re-analysis into new independent Evidence.",
        "exit": "A title and self-contained human-readable summary are ready; SDK will append only machine-record indexes at the end.",
    },
}


INVESTIGATOR_CONTENT_BOUNDARY = (
    "explore local files and directories as needed to resolve the frozen question; reuse prior findings and "
    "unchanged computations when they already answer it, but follow new file paths or search the workspace "
    "whenever the science requires it. Before reading content whose size is not already bounded, first inspect "
    "its scale, structure, and relevant boundaries. Do not inject unbounded raw content into context: prefer "
    "fields, statistics, or a compact reproducible summary emitted by a small analysis, and open only small, "
    "precisely located excerpts that the frozen objective requires."
)


INVESTIGATOR_SKILL_ROUTING = (
    "Before the first related tool execution, load numerical-investigation whenever the current work "
    "requires code execution, numerical calculation, statistical analysis, data checking, task-local "
    "source computation, or another reproducible experiment. Load theory-investigation only when the "
    "work requires nontrivial definitions, theoretical derivation, closure, scaling/limit analysis, "
    "mechanism analysis, or theoretical prediction."
)


def stage_card_text(role: str, stage: str) -> str:
    card = STAGE_CARDS.get((role, stage))
    if not card:
        return f"Stage: {stage}\n"
    role_guidance = (
        f"Investigator skill routing: {INVESTIGATOR_SKILL_ROUTING}\n"
        f"Investigator content boundary: {INVESTIGATOR_CONTENT_BOUNDARY}\n"
        "Do not produce deprecated routing or internal Turn fields.\n"
        if role == "investigator" else ""
    )
    # Investigator Stage Cards are Observer/legacy-stage documentation only.
    # Production Native Goal is composed exclusively in native_goal.py.
    return (
        f"Stage: {stage}\n"
        f"Current objective: {card['objective']}\n"
        f"May do: {card['may_do']}\n"
        f"Must not: {card['must_not']}\n"
        f"{role_guidance}"
        f"Exit / handoff: {card['exit']}\n"
    )


INPUT_COMPARISON_BOUNDARY = (
    "Use actual comparison units and input/control representation. "
    "Marginal ranges alone do not establish joint or conditional support; state only what the observed check establishes. "
    "Do not require universal exact matching. If eligibility needs new science, require a credible scoped route and a scientifically informative insufficient-support outcome, not a check-later plan or claimed proof. "
    "Lead binds key checked/pending input limits (representation, units, domain, comparison sides) in decisive_test; Challenger verifies it beyond rationale/reason."
)


PROCEDURE_CARDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("research_lead", "frontier_proposal"): (
        "The SQLite-backed research graph is your scientific working surface. Start from the current Goal/Contract/Final Gate and injected Scientific Model. Use only the injected agent_graph (or same-stage agent_graph_reuse); never open or parse exploration.canvas. Missing graph context is an error. Read explicit frontier states independently of graph leaves; unmarked means unknown, not closed.",
        "Before proposing, search formal research history across H, Outcomes and External records, beyond declared parents and recent Cycles. Reconstruct the closest prior model or claim, definitions, actual test, verdict, scope and older defining or adverse results from primary reports; titles or citations do not suffice. Use equivalent terms, mathematical descriptions and pagination; one keyword miss or first page does not establish novelty. In why_now/goal_advance state the inherited result, unresolved gap and new discriminating information. Renaming, reparameterizing or changing targets/scope alone does not establish novelty; justify what new knowledge the change enables. If material history is unavailable, expose the gap rather than claim novelty; do not read remaining_goal_gap as a stale directive; stop once the relevant old/new comparison is established, without replaying the whole archive.",
        "Inspect existing inputs and registry/metadata for indispensable prerequisites. For an eligible comparison set, check non-empty coverage, independent units and relevant contrast/common support after the stated controls; a catalog count or a future-check plan is not evidence. Use bounded, cheap input-side checks, without response fitting or outcome-based sample selection. Cite source and observed result in rationale; expose the qualification gap. Check whether independent units, comparator and uncertainty/error budget can distinguish the claimed outcomes rather than merely yield estimates; new derivations and the full decisive test remain Investigator work.",
        INPUT_COMPARISON_BOUNDARY,
        "Do not force continuation along an active branch when it cannot advance the Goal; wording changes alone do not justify a new claim. Use history to form a test with meaningful matching, contrary and inconclusive interpretations. Request Literature only when a blocking external scholarly gap prevents responsible formulation.",
    ),
    ("research_lead", "frontier_maintenance"): (
        "Use agent_graph when supplied, or agent_graph_reuse to reuse the graph from this Cycle and stage's earlier Context. Read the canonical SQLite graph at science_graph.sqlite3 and explicit frontier states; unmarked is not closed, and every relevant H can be a scientific origin even if it already has H descendants. Exclude External nodes from frontier maintenance.",
        "For a detailed graph lookup, use the read-only science_graph_tools command interface supplied in Context: run its command_prefix followed by hypothesis-detail H-XXXX or external-detail E-XXXX. Use this for normalized SQLite detail and full multi-parent lineage instead of hand-parsing dossier files. Read only details that can change the scientific choice; continue using ordinary file access for raw numerical data or full reports. The tools are read-only; never edit graph data through them.",
        "For each relevant H choose active/parked/closed based on the unmet Goal and valid science, not graph degree or whether Outcome is supported/rejected. SDK atomically persists status; zero active H does not imply Goal Completion.",
    ),
    ("challenger", "candidate_review"): (
        "Use candidates, Goal/Contract/Final Gate and SQLite-derived source_hypotheses and typed lineage; never open or parse exploration.canvas.",
        "Reconstruct the closest prior model/claim, actual test, verdict and boundary, including older primary reports outside declared lineage. Compare the proposed scientific difference with that result and adverse evidence; Lead citations do not prove novelty. Try equivalent terms/pages; topical overlap is not novelty or duplication. Explain the old/new comparison in reason; unresolved material history is a qualification gap. Do not demand the new research result at qualification.",
        "If the decisive test explicitly requires a matched or otherwise eligible comparison, independently inspect existing inputs/metadata for coverage, independent units and contrast after stated controls. A catalog count or future-check plan is not evidence. Cite the source and observed result in reason; missing cheap necessary checks leave a qualification gap; do not impose matching when the science does not require it. Keep checks cheap, bounded, input-only and necessary; no response fitting, outcome-based sample selection or full decisive test. New derivations remain Investigator work. Stop reading once the decision cannot change.",
        INPUT_COMPARISON_BOUNDARY,
    ),
    ("challenger", "repair_verification"): (
        "Apply the fixed one-shot checklist to the repaired decisive_test, including its binding clarifications and operational definitions: check they are self-contained, mutually consistent and preserve the original scientific target/scope/estimand; original blockers closed; necessary definitions/inputs and an in-scope route are feasible; required comparison coverage and contrast after controls are supported by observed input-side checks, not catalog counts or future plans; matching, contrary and inconclusive outcomes remain interpretable; no new fatal design flaw. Independently inspect necessary existing inputs/metadata, citing source and observed result in reason; missing cheap necessary checks remain unresolved. Use existing summaries or cheap bounded input-only checks, without response fitting, outcome-based sample selection or the full decisive experiment.",
        INPUT_COMPARISON_BOUNDARY,
        "Return confirmed or unresolved with concise reason. Investigator-solvable prerequisites and weak/open empirical outcomes do not disqualify an otherwise executable, discriminating test. Never invent optional requirements, demand another repair/re-audit, choose H, or undertake Investigator research.",
    ),
    ("research_lead", "hypothesis_display"): (
        "Use only the frozen_hypothesis injected by the SDK. Do not open the graph, dossiers, or Cycle artifacts.",
        "Return claim and indispensable conditions separately so the SDK can validate and compose a faithful, bounded summary with a deterministic epistemic status.",
    ),
    ("research_lead", "candidate_selection"): (
        "Select only among qualified candidates supplied for the current Cycle. Compare their stated Goal advancement and scientific discrimination without redefining their tests.",
        "Return one candidate ID and a concise reason; do not generate another candidate or modify qualification.",
    ),
    ("research_lead", "cycle_synthesis"): (
        "Start from the current Judge verdict and frozen H scope. Consult the existing Scientific Model and only the SQLite-backed prior Outcomes needed to synthesize durable changes.",
        "Write one coherent current scientific picture, preserving established conditions and unresolved core; do not write chronology, frontier state, or future candidate plans into the model.",
    ),
    ("research_lead", "selection_repair"): (
        "Use the supplied frozen parent and each exact Challenger blocker ID; resolve each inside unchanged H/scope/estimand and include only necessary semantic patches.",
        "Keep material checked/pending input limits in the complete decisive_test; audit rationale alone is not binding. Preserve the original scientific scope.",
        "If any blocker requires changing the frozen scientific proposition or cannot be resolved inside it, return cannot_repair (SDK stops unqualified). Do not substitute new Candidate proposals or pre-solve Investigator science.",
    ),
}


def procedure_card_text(role: str, stage: str) -> str:
    steps = PROCEDURE_CARDS.get((role, stage))
    if not steps:
        return ""
    body = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
    return f"Procedure for this stage only:\n{body}\n"


def stage_for(role: str, state: Mapping[str, Any], context: Mapping[str, Any], *, contract_role: str | None = None) -> str:
    if role == "research_lead":
        if context.get("mode") == "frontier_planning":
            raise ValueError("frontier_planning is retired: a new Cycle owns proposing and locking its Hypothesis")
        if context.get("mode") == "cycle_end":
            return "cycle_synthesis"
        if context.get("mode") == "hypothesis_display":
            return "hypothesis_display"
        if context.get("mode") == "frontier_maintenance":
            return "frontier_maintenance"
        if context.get("qualification_error"):
            return "selection_repair"
        if context.get("phase") == "finalization":
            return "candidate_selection"
        return "frontier_proposal"
    if role == "challenger":
        return "repair_verification" if context.get("mode") == "repair_verification" else "candidate_review"
    if role == "investigator" and context.get("mode") == "preserve_research":
        return "preserve_research"
    if role == "investigator":
        if context.get("judge_objections"):
            return "judge_rework"
        if context.get("routing_source") == "literature" or context.get("previous_executor") == "literature" or context.get("previous_capability") == "literature":
            return "integrate_literature"
        return "investigate"
    if role == "literature":
        task = context.get("literature_task") if isinstance(context.get("literature_task"), dict) else {}
        task_type = str(task.get("task_type") or "")
        if task_type not in {"landscape", "targeted_source", "novelty_check"}:
            raise ValueError("Literature requires literature_task.task_type=landscape|targeted_source|novelty_check")
        return task_type
    if role == "judge":
        return "goal_completion" if context.get("mode") == "goal_completion" else "terminal_assessment"
    if role == "reporter":
        return "report"
    return str(context.get("phase") or context.get("mode") or "task")
