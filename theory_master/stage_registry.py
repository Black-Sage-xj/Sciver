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

STAGE_CARDS: dict[tuple[str, str], dict[str, Any]] = {
    ('research_lead', 'frontier_proposal'): {
        "objective": 'Propose one coherent, independently adjudicable scientific claim addressing a consequential unmet Goal, Research Contract, Final Gate or necessary prerequisite. Avoid umbrella programs, filler, preparation-only work and fragments that add no scientific information.',
        "may_do": 'Formulate a specific proposed answer: a defined relation, mechanism, structural assumption, theorem or model/algorithm family, its domain and a discriminating consequence. Explain the substantive change from prior claims and what could contradict it; Goal restatements, extra constraints, renamed parameters and output lists alone are not scientific advances. Where relevant, compare with a simpler explanation or existing method using an appropriate contrast; do not require irrelevant baselines. Response-fitted lookup or interpolation alone is a baseline, not a new mechanism; calibrated parameters remain eligible inside a genuine structural, transfer or counterexample claim. Match uncertainty and proof precision to the scientific distinction claimed. Under the stated design, required decision outcomes must be logically attainable and, for statistical tests, statistically reachable; a merely nonzero, finite or converged estimate need not discriminate the claim. Avoid universal sample sizes or thresholds. Matching, contrary and inconclusive results each need an honest interpretation; a local contrary result refutes H only if it contradicts a necessary local prediction. A useful counterexample, bounded mechanism, negative result or high-risk claim can advance the Goal without completing it. Bind all material definitions, eligibility conditions, uncertainty requirements and outcome rules in hypothesis/scope/decisive_test, not proposal rationale. Indispensable premises need a credible in-scope route; unknown derivations and the decisive experiment remain Investigator work.',
        "must_not": 'Do not select or lock a Candidate before Challenger, change frontier states, reopen closed H, or continue parked H unchanged. A new Candidate from historical work must be scientifically distinct; retesting an active or resolved claim needs new science or a new source of uncertainty, not merely stronger confidence. Do not create display copy or use Literature for optional novelty strengthening; request it only for a blocking external scholarly gap.',
        "exit": 'Return the smallest sufficient set of genuine Candidate options, or a Literature request when a blocking external scholarly gap prevents responsible formulation. SDK owns identity, lineage persistence, qualification routing and Hypothesis Lock.',
        "procedure": (
            'Start from the current Goal/Contract/Final Gate, Scientific Model and injected agent_graph or same-stage agent_graph_reuse. Missing graph context is an error. Frontier labels describe opportunities independently of graph leaves and Judge verdicts; unmarked is unknown, not closed. Consider relevant active H without treating their labels as commands; justify historical pivots by Goal impact.',
            'Apply the actual Goal/Contract/Final Gate scope: require full-sequence coverage or prohibit standalone local targets only when the task explicitly requires it; generation/shell terminology alone establishes neither restriction. If decisive_test depends on comparison eligibility, matching or controls, check the eligible independent units and relevant joint/conditional support; marginal ranges and catalog counts do not establish it. Do not impose matching or joint-support tests on questions that do not require them.',
            "Read all relevant goal_gate_attention rows, including closed H, and follow the linked supported Outcomes to the Goal Judge's full not_passed rationale. Compare each still-relevant missing deliverable with the current Final Gate and actual deliverables; omission in a later rationale does not establish resolution. Local support, whole-H scope, frontier state and Goal completion are distinct. focus_details=[] means no detail was auto-expanded, not no history; cycle_result_pattern summarizes only Cycle-local verdicts. In why_now/goal_advance explain the requirement addressed, or why distinct new science is necessary before an existing deliverable can be corrected; the historical Judge rationale does not choose the next H.",
            'Before proposing, search formal research history beyond declared parents and recent Cycles across H, Outcomes and External records, including pending/parked claims and unadjudicated work. Separate proposals, provisional results and Judge conclusions. Reconstruct the closest prior model/claim, definitions/formula, actual test, verdict, scope and older defining or adverse results from primary reports; titles or citations do not suffice. Use equivalent terms, mathematical descriptions and pagination; one keyword miss or first page does not establish novelty. Preserve valid subresults of rejected work. State what old results already imply, the remaining scientific unknown, what outcomes change understanding and why this gap matters versus credible alternatives; renaming, reparameterizing or changing targets/scope alone does not establish novelty. Expose material unavailable history rather than claim novelty, and stop once the relevant comparison is established.',
            'Inspect cheap necessary existing inputs/metadata before relying on them; cite sources and observed results, distinguish checked from pending assumptions, and expose missing indispensable checks. Assess whether eligible independent units, the comparator and uncertainty/error budget can in principle distinguish the stated outcomes. Keep qualification checks bounded and input-only; do not fit responses, select samples from outcomes, require the new scientific result or execute the decisive test. New scientific prerequisites remain Investigator work when a credible scoped route and informative failure outcome exist.',
        ),
    },
    ('research_lead', 'hypothesis_display'): {
        "objective": 'Faithfully compress the locked Hypothesis for graph display as presentation of an existing scientific decision.',
        "may_do": 'Use only the injected frozen_hypothesis to express the concrete claim and indispensable applicability conditions separately, with a short title. Preserve the frozen meaning and uncertainty.',
        "must_not": 'Do not revise or reinterpret hypothesis/scope/decisive_test, identity, lineage, evidence or verdict; imply support; open files or consult post-lock findings. SDK owns summary composition and epistemic labels.',
        "exit": 'Return the display fields required by the output Contract.',
        "procedure": (
            'Read only the injected frozen_hypothesis; compress its claim and conditions without introducing scientific content.',
        ),
    },
    ('research_lead', 'candidate_selection'): {
        "objective": 'Choose one Candidate from the already-qualified set for this Cycle.',
        "may_do": 'Compare the qualified options by stated Goal advancement, scientific value, discrimination, information gain, executability and proportionate cost. Explain the preference without redefining their scientific commitments.',
        "must_not": 'Do not reopen qualification, choose revise/reject Candidates, regenerate the frontier, alter hypothesis/scope/decisive_test, create criteria or route specialists.',
        "exit": 'Return one supplied qualified Candidate identity and a concise preference reason; SDK performs Hypothesis Lock.',
        "procedure": (
            'Compare only the supplied qualified options and select one unchanged; the approved decisive_test remains the sole Cycle test definition.',
        ),
    },
    ('research_lead', 'selection_repair'): {
        "objective": 'Repair the one supplied revise Candidate once against its frozen Challenger blocker ledger.',
        "may_do": 'Close each exact blocker ID with the minimum clarification or patch preserving the parent hypothesis, scientific scope and estimand. When resolved, supply the COMPLETE revised decisive_test with every binding clarification, definition, eligibility condition, checked/pending input limit and outcome rule; blocker_closures explain the repair but never supply missing binding science.',
        "must_not": 'Do not substitute a parent, propose another direction, expand scope/estimand, restart frontier search, add optional strengthening or solve Investigator-owned research. If any blocker needs a different scientific proposition or has no resolution inside the frozen boundary, return cannot_repair.',
        "exit": 'Return either the blocker-complete repair for independent verification or cannot_repair with a reason. cannot_repair remains unqualified, does not lock H and does not enter Investigator.',
        "procedure": (
            'Compare the frozen parent and ledger with the patch: account for every original blocker by exact ID, preserve the original scientific target, and make the full revised decisive_test self-contained. Do not rely on audit rationale or proposal background as hidden conditions. Preserve any task-specific coverage or comparison conditions already binding in the parent; do not add sequence or matching restrictions inferred only from terminology.',
        ),
    },
    ('research_lead', 'cycle_synthesis'): {
        "objective": 'Synthesize the terminal Judge decision into durable scientific understanding across relevant Cycles.',
        "may_do": 'Use the locked Hypothesis, current Judge verdict/rationale, existing Scientific Model and relevant prior Outcomes. Preserve still-valid premises, scope, uncertainty and conflicts; distinguish established conclusions from unresolved questions. Keep the model unless durable understanding changes; when replacing it, write one concise coherent current scientific picture.',
        "must_not": 'Do not change Goal/Contract/Final Gate, infer stronger support than adjudicated, create next-Cycle Candidates, put frontier state or a Cycle chronology in the model.',
        "exit": 'Return the scientific synthesis and keep/replace model decision required by the Contract; SDK persists the result.',
        "procedure": (
            'Start from the current verdict and frozen scope; consult only prior Outcomes needed to reconcile durable conclusions and open questions.',
        ),
    },
    ('research_lead', 'frontier_maintenance'): {
        "objective": 'Maintain opportunity states of existing H after this Cycle.',
        "may_do": 'Assess remaining Goal opportunities using Goal/Contract/Final Gate, Scientific Model, Judge decision, synthesis and relevant H/Outcomes. Choose active/parked/closed independently of graph degree, descendants or local supported/rejected/open Outcomes. Unmarked means unknown, not closed. Exclude External nodes and external_reference edges.',
        "must_not": 'Do not create H or Candidates, choose the next Cycle, change scientific_status, Judge verdict or Scientific Model, or infer Task completion from zero active H or all closed frontiers.',
        "exit": 'Return only the existing-H frontier updates required by the Contract; SDK persists them without clearing non-leaf labels.',
        "procedure": (
            'Use the supplied agent_graph or same-stage agent_graph_reuse and explicit frontier states. For details that can change a frontier judgment, use the read-only science_graph_tools command_prefix with hypothesis-detail H-XXXX to obtain canonical H/Outcome details and multi-parent lineage; raw reports or numerical data remain accessible by ordinary file tools when needed. Assign each update a scientific reason grounded in remaining Goal opportunities.',
        ),
    },
    ('challenger', 'candidate_review'): {
        "objective": 'Independently qualify each supplied candidate for an unmet Goal/Final Gate or a necessary prerequisite before selection. Assess scientific value and the design, not whether the hypothesis is true.',
        "may_do": 'Review frozen_objective as the binding science; proposal_context only explains priority and provenance. Require a specific scientific commitment and discriminating consequence, not a Goal restatement or deliverable list. Compare its new information with the closest prior claim and relevant simpler explanations or existing methods; a renamed fit, extra constraints or descriptive reproduction alone is insufficient. Judge hypothesis-test alignment, falsifiability, scope, inference and essential routes. Check logical reachability of the claimed decision; for empirical designs also assess independent units, uncertainty/error budget and statistically unreachable outcomes under the stated design. For existence claims, check the admissible family and logical decision: finite failed search is not general refutation. Thresholds need a scientific or design basis; otherwise use an informative estimate or bound with uncertainty. Matching, contrary and inconclusive results must retain scientific meaning; a local result bears on a broader claim only within its actual logical implication. Derive scope obligations from the actual Goal/Contract/Final Gate and decisive_test. Apply full-sequence or no-local-window restrictions only when those requirements establish them; generation/shell terminology alone does not. Joint/conditional support checks apply only to actual empirical comparisons needing those controls; purely theoretical or existence proofs need no data matching or statistical sample requirement.',
        "must_not": 'Do not demand optional stronger claims, irrelevant baselines, arbitrary sample sizes/cutoffs or extra robustness. An unfinished proof, new derivation, uncertain effect, proportionate research cost, or eventual negative/open result is not itself a blocker when a credible in-scope route and meaningful test remain. Do not demand the research answer at qualification or perform the full decisive test.',
        "exit": 'Use qualified when the candidate adds Goal-relevant scientific information without a material blocker and has a credible essential route. Use revise only when every blocker can be closed inside unchanged hypothesis/scope/estimand; identify all visible independent blockers, not a single convenient defect. Use reject when the scientific target must change, an essential route is demonstrably unavailable, the proposal adds no scientific information beyond an active/resolved claim without new uncertainty, or the target is Goal-unrelated. Reject the current design, not its general topic; topical overlap alone is insufficient. Return the per-candidate decision in the Contract projection; selection follows separately.',
        "procedure": (
            'Read current Goal/Contract/Final Gate and goal_gate_attention with linked Outcomes, including closed H. Compare why_now/goal_advance against each still-relevant prior Goal Judge not_passed gap. If adjudicated work already permits a missing deliverable, require a concrete reason new science must precede supplying it. A later omission does not prove an earlier gap closed; task-level not_passed is not a rejection of the source H or an instruction selecting the next target.',
            'Independently reconstruct the closest prior model/claim, definitions/formula, actual test, verdict, scope and relevant adverse results from primary reports, beyond declared parents and recent Cycles. Include pending/parked claims and provisional work; separate them from Judge conclusions and preserve valid subresults of rejected work. Search equivalent terms and relevant pages rather than infer novelty from one miss. State the old/new comparison and Goal value in reason; unavailable material history is a qualification gap. Stop once the comparison needed for this decision is established.',
            'Independently verify cheap necessary input/metadata conditions bound by decisive_test; cite the source and observed result in reason. Check required independent units and any required comparator/contrast without dropping frozen restrictions; do not impose matching on a design that does not require it. Catalog counts or a check-later plan do not establish necessary eligibility. Keep checks bounded and input-only: no response fitting, outcome-based sample selection or Investigator research. If eligibility itself needs new science, assess a credible scoped route and an informative failure outcome rather than require that result in advance.',
        ),
    },
    ('challenger', 'repair_verification'): {
        "objective": 'Verify the SDK-closed repaired candidate exactly once after Lead revision. Check the original blocker ledger and the finite necessary conditions below, preserving the original qualification and selection boundaries.',
        "may_do": 'Compare the supplied original_candidate, repaired_candidate and original_blockers using current Goal/Contract/Final Gate, supplied graph context and authorized existing assets as needed. Confirm only if all original blockers are closed and the repaired test is self-contained and consistent while hypothesis, scope, estimand and Goal advance remain unchanged.',
        "must_not": 'Do not reopen candidate_review, re-rank candidates, invent optional criteria, demand stronger claims/extra robustness or suggest another repair. Do not treat an unfinished in-scope derivation, uncertain effect or eventual negative/open result as failure when a meaningful executable test remains. Do not execute the full decisive test or demand Investigator-owned results.',
        "exit": 'Return confirmed only after every original blocker and fixed necessary check passes; this authorizes Investigation, not scientific success. Otherwise return unresolved with the concrete remaining blocker or fatal fixed-check failure. Give no further repair instructions; this Stage runs once. Output fields and allowed verdicts are defined in the Contract projection.',
        "procedure": (
            'Apply only this fixed checklist: (1) all original blockers are closed; (2) binding definitions and the complete decisive_test are consistent and preserve the original scientific target/scope/estimand; (3) essential inputs/definitions and a credible in-scope route exist, including a non-empty comparison set only where required; (4) the stated design permits the claimed logical/statistical discrimination and meaningful matching, contrary and inconclusive interpretations; (5) the revision exposes no fatal flaw in those same conditions. Do not renew the full novelty or Goal-priority review.',
            'For a necessary precondition, use existing summaries or bounded cheap input-side checks and cite source/observed result in reason; omitted cheap necessary checks remain unresolved. Apply frozen eligibility restrictions without relaxing them for coverage; no response fitting, outcome-based sample selection or full experiment. A genuinely unavailable essential route or unreachable decisive outcome is a flaw; unfinished science with a credible route is Investigator work. Apply full-sequence restrictions and joint/conditional support only when established by the actual Goal/Contract/Final Gate or frozen test; terminology alone adds no obligation. Purely theoretical or existence proofs need no empirical matching or statistical sample requirement.',
        ),
    },
    ('investigator', 'preserve_research'): {
        "objective": 'After explicit Supervisor or operator authorization, preserve already obtained unadjudicated scientific work in one Investigator Stage. This is preservation, not execution or completion of the frozen decisive_test.',
        "may_do": 'First establish A / Science from existing Cycle artifacts: source-grounded provisional observations, their conditions and limits, bounded failures of attempted methods, the remaining prerequisite and the exact unexecuted parts of decisive_test. Then produce B / Presentation in the same response by compressing only that established content into a short unadjudicated display. Existing source checks are allowed; compression adds no evidence or claim.',
        "must_not": 'Do not run new experiments, derivations or research computations, reopen investigation, revise the frozen object, infer a Judge verdict or decide to stop the Hypothesis. Do not request another role or a second presentation model turn. SDK owns source validation, rendering, Canvas projection, frontier state and Cycle closure.',
        "exit": 'Return one preservation deliverable using the current Contract. Distinguish established observations from missing work and keep presentation within the evidence of A. SDK performs projection and closure without downstream Agent calls; a future independently started Lead may consult the source without being directed by this Stage.',
    },
    ('investigator', 'investigate'): {
        "objective": 'Describe full investigation of the frozen decisive_test. In production, the Native Goal supplies the operative scientific completion conditions; this observer/legacy Stage does not replace them.',
        "may_do": 'Decompose the frozen test into necessary prerequisites and its final discriminating comparison. Qualify definitions and inputs, derive or implement predictions, perform the independent checks appropriate to the scientific object and synthesize the outcome. Deferred in-scope prerequisites remain Investigator work; Lead need not prescribe technical steps. Before costly work, estimate feasibility and scientific value; abandon disproportionate methods and try valid bounded alternatives.',
        "must_not": 'Do not submit a partial theorem, diagnostic, source discovery or invalid sample as completion while the frozen comparison remains unfinished. Failed methods do not prove impossibility. Optional robustness beyond the frozen test is not required, and worst-case bounds do not justify unbounded compute. Do not request native blocked for scientific difficulty.',
        "exit": 'Follow the current Native Goal and SDK delivery interface. The combined findings must address the whole frozen test sufficiently for independent Judge review: support, falsification or a genuinely established non-identifiability or inconclusive result after the required comparison. State material conditions and limits in each scientific statement; unfinished science remains unfinished.',
    },
    ('investigator', 'integrate_literature'): {
        "objective": 'Describe integration of verified Literature Evidence into the ongoing frozen research. In production, the Native Goal determines completion; this observer/legacy Stage adds no separate milestone.',
        "may_do": 'Verify source provenance and applicability, then perform newly feasible definitions, derivations, numerical work and decisive comparisons under the same frozen object. Continue from retained results and resolve the outstanding research need.',
        "must_not": 'Do not promote an unverified source to scientific fact, restart completed work, or replace an outstanding numerical or theoretical test with a source summary.',
        "exit": 'Literature integration alone is not research completion. Follow the current Native Goal and SDK delivery interface; complete the remaining frozen research and present self-contained findings with honest limits.',
    },
    ('investigator', 'judge_rework'): {
        "objective": 'Describe resolution of the actual requested_resolution in each authorized Judge objection. In production, the Native Goal binds those requests and supplies completion conditions; this observer/legacy Stage does not redefine them.',
        "may_do": 'Continue in the retained Investigator thread. Decompose the exact Judge request into missing prerequisites and comparisons, perform the necessary in-scope work, and check that the evidence answers that request. If old data or methods are ineligible, pursue valid alternatives; estimate feasibility before costly work and abandon disproportionate methods without replacing the requested resolution.',
        "must_not": 'Mentioning an objection in a Finding does not resolve it. Do not substitute a weaker surrogate, replay old evidence as new, delegate the scientific resolution back to Judge, or treat failed attempts as proven impossibility. Do not request native blocked for scientific difficulty.',
        "exit": 'Follow the current Native Goal and SDK delivery interface. The combined findings must materially resolve the exact objection through its decisive comparison or a rigorous falsification, non-identifiability or impossibility under the frozen conditions. If actionable required work remains, continue; qualifications in a partial statement do not replace the missing test.',
    },
    ('literature', 'landscape'): {
        "objective": 'Map the external scholarly field around the assigned research frontier as an account of established routes, representative work and open questions.',
        "may_do": 'Screen the indexed metadata and abstracts for breadth: identify terminology, major technical routes, seminal or representative papers, method evolution, open problems, relevance to the current research and a useful reading order. Expand to bounded matching records or abstracts only where the map needs them. Verify the few original sources whose claims materially affect the current research decision. Read the research-lit skill when structured multi-source synthesis is needed.',
        "must_not": 'Do not full-read every retrieved paper, equate citation count with scientific importance, or present metadata-level field coverage as claim-level verification.',
        "exit": 'Return a compact field map with traceable representative sources, explicit coverage limits and open questions; keep source-verified scientific claims distinct from metadata/abstract-level mapping.',
    },
    ('literature', 'targeted_source'): {
        "objective": 'Verify the exact external source or claim required by the assigned gap: a definition, theorem, counterexample, method origin or attribution.',
        "may_do": 'Honor source_lock when supplied. After the common evidence index, inspect retrieval/paper_focus.json for the focused source summary, then reading/reading_index.md and relevant traces/*/paper_trace_report.md; consult retrieval/errors.log if materialization or access is unclear. These are navigation aids, not source verification. The compact candidate index (up to six entries) and focused summary (up to 1800 characters) are initial navigation bounds, not scientific evidence or a prohibition on necessary bounded expansion. Where needed, extract only matching DOI/title records and relevant abstracts from retrieval/papers.json, or consult retrieval/papers_top.json. Trace the claim to its original source, read the relevant materialized source passage and verify its locator, quotation, applicability and limitations. Read the tm-scholar skill for citation tracing and source verification.',
        "must_not": 'Do not restart a broad topic review or continue searching after the source question is answered, or after available material cannot answer it without a new search lead. Do not turn an unavailable source or metadata-only trace into a verified theorem or full-text claim.',
        "exit": 'Report what the assigned source actually establishes, any conflict or partial verification, and the remaining source question. If the original source is inaccessible, state that limit and the access level of the material actually reviewed.',
    },
    ('literature', 'novelty_check'): {
        "objective": 'Position the assigned result or hypothesis against external prior work and identify overlap, differences and novelty or priority risks.',
        "may_do": 'Use the compact corpus index to locate the closest prior work by scientific claim and method, rather than popularity alone. Inspect bounded matching records and relevant source passages to compare assumptions, scope, result and method; trace material overlap or priority claims to original sources when available. Separate verified comparisons from metadata/abstract-level leads. Identify required citations and explain unresolved uncertainty in apparent novelty. Read the novelty-check skill for claim decomposition and comparison.',
        "must_not": 'Do not infer exhaustive novelty from search silence, hide conflicting prior work, or give a continue/abandon verdict on the research.',
        "exit": 'Return traceable closest neighbors, supported similarities and differences, priority risks and the limits of the search and source verification, so Research Lead or Judge can use the positioning without mistaking it for a novelty verdict.',
    },
    ('judge', 'terminal_assessment'): {
        "objective": 'Adjudicate the exact locked Hypothesis through its frozen decisive test using only the supplied compact scientific result and relevant already-adjudicated predecessor evidence.',
        "may_do": "Assess terminality only for the exact locked Hypothesis and its frozen scope, not for whether the overall research topic still has future questions. First determine whether the current-Cycle result resolves the frozen decisive test, including its evidence-identity requirements; prior adjudicated results can support cumulative reasoning but fulfill a requirement for new or independent Evidence only when the test permits reuse. Then determine what that result establishes about the locked Hypothesis. Preserve each Finding's applicability, uncertainty and independently validated provenance. Use milestone_advanced only when the decisive_test is resolved but a requirement logically required by the current locked Hypothesis itself remains unresolved. Request rework when a necessary discriminating result is missing and executable work remains within the unchanged hypothesis/scope. Name one missing SCIENTIFIC DECISION RESULT, even if it requires several linked prerequisites, simulations or independent network comparisons; leave method choice to Investigator and identify whether external scholarship is necessary. A terminal open requires that the frozen test has actually been executed within its evidence/independence/error constraints and remains scientifically inconclusive. Partial findings, an unexecuted test, or operational/resource/eligibility obstacles do not establish terminal open; blocked or exhausted non-terminal work remains an SDK execution state.",
        "must_not": 'Do not request raw files, scripts, proofs, Evidence Manifest, Coverage Matrix, Judge Packet, artifact paths or hashes. Do not relabel historical re-analysis as newly generated or independent Evidence. Do not promote a positive local result into whole-hypothesis support or reject the whole hypothesis from a merely inconclusive local result. Do not block closure or justify milestone_advanced for stronger hypotheses, broader scope, or optional scientific strengthening (extra robustness, higher confidence, future propositions), implementation preferences, or operational/resource concerns unless the locked Hypothesis and frozen scope logically require them. Do not output audit inventories, per-Evidence assessments, search hints or a rework plan.',
        "exit": 'Give the scientific ruling and its rationale, or one objection identifying the missing discriminating result when rework is required. Use the Contract projection for output fields and mutually exclusive branches.',
    },
    ('judge', 'goal_completion'): {
        "objective": 'Decide whether the existing whole-Task trajectory satisfies the current Final Gate in research_plan.md. The task-level Goal Contract consists of its Goal, Research Contract, Final Gate and Required Context.',
        "may_do": 'Read the complete research_plan.md through research_plan_access. Identify its completion-critical quantities, predictions, transformations, deliverables, validation conditions and task-material boundaries. Use the SQLite-backed agent_graph and formal adjudication records as scientific authority for H/Cycle Outcomes, explicit frontier states, lineage and detail_ref links. Follow only the direct local detail links needed to verify a concrete deliverable against the Final Gate. If SQLite context is missing, surface a context error. Return passed only when the current Final Gate is satisfied; otherwise return not_passed with the scientific reason. A supported H or Cycle does not by itself establish Goal completion.',
        "must_not": 'Do not read or parse exploration.canvas, which is a display projection. Do not substitute a successful local hypothesis, surrogate metric or held-out comparison for an original Goal deliverable. Do not invent requirements beyond the current research_plan.md or treat optional robustness, external generalization, generic future science or work outside its Goal Contract as blockers. Do not perform broad repository exploration or propose a next Candidate. Do not produce a next-step plan. Do not output requirement ledgers, artifact inventories or a new research plan.',
        "exit": 'Give a concise assessment of whether the existing trajectory satisfies the Final Gate. A negative assessment explains the unmet requirement without requesting a next step; use the Contract projection for output fields.',
    },
    ('reporter', 'report'): {
        "objective": 'Write the complete human-readable scientific report for this Cycle after Judge adjudication and Scientific Model commitment.',
        "may_do": 'Use the supplied hypothesis, judge_decision, scientific_synthesis, scientific_model_summary and evidence_scope_digest to explain the question, result, decisive Evidence, interpretation, relevant counter-explanations and model-level meaning. Read the report-writing skill for narrative, formula and figure conventions. Present scope, assumptions, uncertainty and unresolved questions in scientific prose. Distinguish the origin of the scientific assets from the Cycle performing the analysis, including historical re-analysis. Cite notes with vault-relative wikilinks, repository artifacts with repository-relative paths, and Cycle artifacts relative to cycle_dir.',
        "must_not": 'Do not turn historical re-analysis or a new Evidence record into newly generated or independent data, experiments, simulations or samples. Do not dump structured records, Agent deliveries, machine metadata, runtime logs or internal field names into the narrative. Do not assess overall Goal completion or introduce a next-frontier plan.',
        "exit": 'Deliver a content-bearing title and the complete self-contained Markdown report through the Contract projection. Its summary field carries the report body, not merely a short abstract. The SDK persists this narrative and appends machine audit/reproduction indexes; no separate Canvas-writing handoff follows.',
    },
}


def stage_card_text(role: str, stage: str) -> str:
    card = STAGE_CARDS.get((role, stage))
    if not card:
        return f"Stage: {stage}\n"
    # Normal Investigator research uses Native Goal; preserve_research uses this Stage renderer.
    return (
        f"Stage: {stage}\n"
        f"Current objective: {card['objective']}\n"
        f"May do: {card['may_do']}\n"
        f"Must not: {card['must_not']}\n"
        f"Exit / handoff: {card['exit']}\n"
    )


def procedure_card_text(role: str, stage: str) -> str:
    """Compatibility accessor for a Stage's embedded procedure, not a separate registry."""
    card = STAGE_CARDS.get((role, stage))
    steps = card.get("procedure", ()) if card else ()
    if not steps:
        return ""
    body = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
    return f"Procedure for this stage only:\n{body}\n"






def stage_definition_text(role: str, stage: str) -> str:
    """Render one complete Stage definition, including its embedded procedure."""
    return stage_card_text(role, stage) + procedure_card_text(role, stage)


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
