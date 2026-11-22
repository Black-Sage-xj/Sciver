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
        "objective": 'Propose one Cycle-decidable proposition for a consequential unmet Goal or necessary prerequisite. A broad task may advance through a necessary subclaim whose positive or contrary result advances the Goal. For a method-only milestone, the current decisive result is the reviewable method deliverable.',
        "may_do": 'Give the claim or requested deliverable, scope and decisive_test. Bind the definitions and outcome rules needed to judge it. A task asking for reproduction, prediction or a specified transformation can be advanced by that result itself. For a discovery claim, explain the substantive change from prior claims and, where relevant, compare with a simpler explanation or existing method using an appropriate contrast. Match uncertainty and proof precision to the scientific distinction claimed; do not require irrelevant baselines. Interpret supporting, contrary and inconclusive results within the stated scope. A useful negative result or high-risk claim can advance the Goal without completing it; a local contrary result refutes H only if it contradicts a necessary local prediction.',
        "must_not": 'Do not select or lock a Candidate before Challenger, change frontier state, reopen closed H or continue parked H unchanged. Avoid umbrella programs, filler, preparation-only work and repeats that leave no task requirement unresolved. Do not create display copy or make Literature mandatory each Cycle. Literature supplies external evidence, not the scientific proposition or its qualification.',
        "exit": 'Return 1–3 defensible Candidates, a focused Literature request, candidates=[] with blocking_reason, or the applicable no_new_science disposition. SDK owns identity, lineage persistence, qualification routing and Hypothesis Lock.',
        "procedure": (
            'Use current Goal/Contract/Final Gate, Scientific Model and injected agent_graph or same-stage agent_graph_reuse. Frontier labels describe opportunities independently of graph leaves and Judge verdicts; unmarked is unknown, not closed. Consider relevant active H without treating their labels as commands; justify historical pivots by Goal impact.',
            'Read all relevant goal_gate_attention rows, including closed H, and linked Outcomes with the full not_passed rationale. Compare open requirements with actual deliverables. A later omission does not establish resolution. Local support, whole-H scope, frontier state and Goal completion are distinct.',
            'Search relevant H, Outcomes and External records beyond declared parents and recent Cycles, including pending/parked claims and unadjudicated work. Separate proposals, provisional results and Judge conclusions. Check the closest prior claim and competing or adverse evidence in primary reports; preserve valid subresults. Use equivalent terms and pagination when needed; stop once the relevant comparison is established. State what remains to be done for this task, including any requested replication. Demand novelty only if the task calls for it.',
            'Use short source-grounded transition.prior_gap and transition.current_change for the unresolved requirement and current test. why_now explains priority and goal_advance the Goal impact. A root H starts from the Goal unknown; cite actual source contributions. recent_execution_limits and recent_qualification_stops inform feasibility without refuting prior H.',
            'Inspect cheap necessary existing inputs/metadata, cite sources and observed results, distinguish checked from pending assumptions, and expose missing indispensable checks. Verify any cited value against the source object, definition, conditions and units. If decisive_test depends on comparison eligibility, matching or controls, check the required units, contrast and uncertainty. Estimate cost when it affects feasibility. Allow a bounded technical retry after a failed read unless the task limits it; keep the scientific test unchanged. A credible scoped route and informative failure outcome suffice for Investigator-owned work; unknown derivations and the decisive experiment remain Investigator work. Missing execution inputs are not scientific inconclusiveness.',
            "For empirical Candidates declare evaluation_materials with the identities and actual uses of the supplied materials; use [] for non-empirical work. Consult evaluation_history and primary reports. Use protected_final only for independence required by the original task or a faithful frozen test. Prior feedback or unknown history cannot establish an untouched partition. Ordinary reuse and calibration follow the task's own conditions.",
            'Use bounded scientific scoping when needed. When scholarship could inform mechanisms, assumptions or design, allow one focused Literature detour before returning candidates. For missing indispensable external definitions, request targeted_source via needs_literature=true, with its DOI/URL in new_search_lead. Investigator has no general external Literature route. Use returned passages/access limits, not metadata as input and never repeat an exhausted detour. Use landscape to map routes, targeted_source for a known claim, novelty_check for overlap. Local sources sufficient for this decision do not require an external search.',
            'For a Literature request supply search_queries and relevance_terms for the same scientific question, using source vocabulary, including English queries for English-language indexes where appropriate. Queries plan retrieval; they are not evidence, a scientific result or another Literature detour.',
            'If only delivery correction or unavailable indispensable materials remain, return no_new_science with the unmet requirement and source basis. Use SDK/operator publish-delivery for existing files or repair-delivery on an existing terminal Cycle for a delivery correction. Distinguish merely publishing a result from producing an outstanding result required by the task.',
        ),
    },
    ('research_lead', 'hypothesis_display'): {
        "objective": 'Faithfully compress the locked Hypothesis for graph display as presentation of an existing scientific decision.',
        "may_do": 'Use only the injected frozen_hypothesis to express the concrete claim and indispensable applicability conditions separately, with a short title. frozen_reasoning supplies already-frozen Lead motivation and source identities only to clarify the existing meaning; the SDK displays its transition verbatim separately. Preserve the frozen meaning and uncertainty, including the distinguishing scientific question relative to its sources when supplied.',
        "must_not": 'Do not revise or reinterpret hypothesis/scope/decisive_test, identity, lineage, evidence or verdict; imply support; open files or consult post-lock findings. SDK owns summary composition and epistemic labels.',
        "exit": 'Return the display fields required by the output Contract.',
        "procedure": (
            'Read only the injected frozen_hypothesis and frozen_reasoning; compress the claim and conditions without introducing scientific content, inventing a missing transition, or rewriting the frozen reasons.',
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
            'Account for every original blocker by exact ID and make the full revised decisive_test self-contained. Include binding conditions in the test itself; proposal background and blocker_closures cannot supply missing conditions.',
        ),
    },
    ('research_lead', 'cycle_synthesis'): {
        "objective": 'Synthesize the terminal Judge decision into durable scientific understanding across relevant Cycles.',
        "may_do": 'Use the locked Hypothesis, current Judge verdict/rationale, existing Scientific Model and relevant prior Outcomes. Preserve still-valid premises, scope, uncertainty and conflicts; distinguish established conclusions from unresolved questions. Keep the model unless durable understanding changes; when replacing it, write one concise coherent current scientific picture.',
        "must_not": 'Do not change Goal/Contract/Final Gate, infer stronger support than adjudicated, create next-Cycle Candidates, put frontier state or a Cycle chronology in the model.',
        "exit": 'Return the scientific synthesis and keep/replace model decision required by the Contract; SDK persists the result.',
        "procedure": (
            'Reconcile previous_scientific_model (its complete summary and input version) with the current verdict and frozen scope. Preserve earlier results still applicable to the Goal, including the tested prediction, metric, denominator, weighting and contrast when they affect meaning. Retain broader conclusions only with their separate logical or evidential basis. Consult prior Outcomes when needed to resolve a concrete conflict. Replace superseded material and compress the whole into one current scientific picture.',
        ),
    },
    ('research_lead', 'frontier_maintenance'): {
        "objective": 'Maintain opportunity states of existing H after this Cycle.',
        "may_do": 'Assess remaining questions inside each H using Goal/Contract/Final Gate, Scientific Model, Judge decision, synthesis and relevant H/Outcomes. Choose active/parked/closed independently of graph degree, descendants or local supported/rejected/open Outcomes. Unmarked means unknown, not closed. Exclude External nodes and external_reference edges.',
        "must_not": 'Do not create H or Candidates, choose the next Cycle, change scientific_status, Judge verdict or Scientific Model, or infer Task completion from zero active H or all closed frontiers.',
        "exit": 'Return only the existing-H frontier updates required by the Contract; SDK persists them without clearing non-leaf labels.',
        "procedure": (
            'Use the supplied agent_graph or same-stage agent_graph_reuse and explicit frontier states. For details that can change a frontier judgment, use the read-only science_graph_tools command_prefix with hypothesis-detail H-XXXX to obtain canonical H/Outcome details and multi-parent lineage; raw reports or numerical data remain accessible by ordinary file tools when needed. Assign each update an H-scoped remaining_question. Active means executable now; parked requires a concrete dependency. Closed means no executable question remains inside this H, even when the overall Goal is incomplete. Reopening requires a new source-grounded reopen_basis; Goal incompletion alone cannot reopen it. Return only changed states or materially changed opportunity descriptions; [] is valid. A distinct follow-up claim belongs in a new H, using the closed H as a source.',
        ),
    },
    ('challenger', 'candidate_review'): {
        "objective": 'Independently qualify each supplied candidate by its contribution to an unmet task requirement and the feasibility of the currently authorized Cycle deliverable. A requested reproduction, prediction or specified procedure is eligible on its own terms. For a method-only milestone review the requested design at this stage.',
        "may_do": "Review frozen_objective as the binding science; proposal_context explains priority and provenance. Check alignment with the task, consistency of definitions, and whether the proposed test can establish the claim in its stated scope. Compare with prior work to identify what remains unresolved. Assess an alternative explanation, comparator, uncertainty or cost when the task or the candidate's own inference depends on it.",
        "must_not": 'Do not demand optional stronger claims, irrelevant baselines, arbitrary sample sizes/cutoffs or extra robustness. An unfinished proof, new derivation, uncertain effect or eventual negative/open result is not itself a blocker when a credible in-scope route and meaningful test remain. Account for an inconclusive outcome where the proposed test permits one. Do not demand the research answer at qualification or perform the full decisive test.',
        "exit": 'Use qualified when the current design can address the stated requirement without a material blocker. Use revise when every blocker can be closed inside unchanged hypothesis/scope/estimand; list all visible independent blockers. Use reject when the design cannot meet that requirement, an essential route is demonstrably unavailable, or its repair would require changing the scientific target. Identify the specific missing inference or task requirement. Candidate selection follows separately.',
        "procedure": (
            'Return scientific_checks for source_fidelity, definition_consistency, discriminating_test and evidence_identity. Cite the relevant primary-source fact or explain inapplicability. Match referenced results to the actual object, definition, conditions and units. If reversing an earlier objection, identify the changed fact.',
            'Review evaluation_materials against evaluation_history and primary reports. Require evidence of unused material only for a protected_final claim authorized by the original task or its faithful frozen test. Prior feedback or missing history cannot establish independence. Accept ordinary task-permitted calibration and evaluation.',
            'Read current Goal/Contract/Final Gate and goal_gate_attention with linked Outcomes, including closed H. Compare why_now/goal_advance with remaining not_passed gaps and transition.prior_gap/current_change with the actual source verdicts. Check recent_execution_limits and recent_qualification_stops for changed feasibility. Wording quality alone is not a scientific qualification blocker.',
            'Compare the closest relevant work from primary reports beyond declared parents and recent Cycles, including pending/parked claims and provisional work; separate them from Judge conclusions. Preserve valid subresults and consider adverse evidence. Stop once the comparison needed for this decision is established. Repeating a task-required procedure can resolve an outstanding deliverable. Mere topical overlap does not establish duplication; an existing complete result may only need delivery.',
            'Independently verify cheap necessary input/metadata conditions and cite the source and observed result. Check availability or credible authorized acquisition for indispensable inputs. A DOI/URL alone is no acquisition route for a local-only Investigator; verify necessary source passages or a scoped route available to the actual executor. Assess whether the allowed route can distinguish the stated outcomes. Check statistical reachability if the inference depends on it, including the relevant units, effect and uncertainty. Keep checks bounded and input-only; do not fit responses or select samples from outcomes. A bounded technical retry can correct a bad read within the unchanged test unless the task forbids it. Missing means to run the test are operational, not scientific inconclusiveness. An execution limit is not a scientific refutation.',
        ),
    },
    ('challenger', 'repair_verification'): {
        "objective": 'Verify the supplied repaired candidate once against the original blocker ledger.',
        "may_do": 'Compare original_candidate, repaired_candidate and original_blockers with the current task and authorized evidence. Confirm that every original blocker is closed and the complete revised decisive_test preserves the hypothesis, scope and estimand.',
        "must_not": 'Do not reopen candidate_review, re-rank candidates, add optional criteria or suggest another repair. Do not execute the full decisive test or demand Investigator-owned results.',
        "exit": 'Return confirmed if the blockers are closed and the repaired test is consistent and feasible; this authorizes Investigation. Otherwise return unresolved with the remaining blocker or the concrete fatal flaw introduced in the same necessary checks.',
        "procedure": (
            'Check that blocker closures agree with the binding revised test. Recheck only the necessary route affected by the repair, using primary sources or bounded input-side checks. Are the changed definitions, source conditions, inputs and decision rules sufficient for the unchanged target? A DOI/URL alone is no acquisition route for a local-only Investigator; verify necessary source passages or a scoped route available to the actual executor. An inability to execute is not a scientific inconclusive result; unfinished science with a credible route is Investigator work.',
            'For an evidence-identity blocker, check the actual use record against the independence the task or faithful frozen test requires. A new name or lock cannot erase prior feedback. Review the authorized deliverable at its own stage. Do not renew the full novelty or Goal-priority review.',
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
        "exit": 'Return a compact field map with traceable representative sources, explicit coverage limits and open questions. For pre-proposal scoping, identify source-backed distinctions and assumptions relevant to the assigned question, without proposing or selecting a Hypothesis; keep source-verified claims distinct from metadata/abstract-level mapping.',
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
        "objective": 'Adjudicate the exact locked Hypothesis through its frozen decisive test using only the supplied compact scientific result and applicable prior adjudicated findings.',
        "may_do": 'Assess terminality only for the exact locked Hypothesis and its frozen scope. Decide whether the test was executed and what its result implies. Carry forward applicable prior findings with their actual origin and uncertainty. A contrary result refutes a claim if it contradicts a necessary prediction. Use milestone_advanced only when the decisive_test is resolved but a condition logically required by the current locked Hypothesis itself remains unresolved. A terminal open requires that the test has actually been executed and remains scientifically inconclusive.',
        "must_not": 'Do not promote a positive local result into whole-hypothesis support. Future questions and optional strengthening do not block scientific closure. Resource or execution problems do not establish terminal open. Do not request raw files. Do not output audit inventories.',
        "exit": 'Return the ruling and rationale under the Contract. If necessary work is still executable within the frozen scope, request one missing scientific decision result, even if it needs several linked prerequisites. Ground each objection in a specific necessary frozen requirement and indicate whether external scholarship is necessary. The valid assessment ends this Cycle’s experiment. The SDK preserves your original feedback for the next Cycle’s Research Lead, who proposes the linked hypothesis for normal qualification.',
        "procedure": (
            'Assess the actual definitions, comparison and uncertainty. Distinguish frozen rules, choices made before fitting, and later changes. Explain any material effect on inference.',
            'Judge ordinary validation under its stated protocol. For a protected evaluation required by the task or frozen test, verify the required separation against exposure history. Relocking exposed data cannot restore independence. For ordinary comparisons, missing stronger SDK attestation or origin_unverified alone does not invalidate an executed result.',
        ),
    },
    ('judge', 'goal_completion'): {
        "objective": 'Decide whether the whole trajectory meets the task-level Goal Contract in research_plan.md. Read the complete plan through research_plan_access and interpret its Goal, Research Contract, Final Gate and Required Context against the original task.',
        "may_do": 'Use the SQLite-backed agent_graph and formal adjudications as authority. Follow direct detail links for necessary checks. Surface a context error if SQLite context is missing. A supported, rejected or scientifically terminal open Cycle may contribute to completion. A negative result can resolve a required question. Assess requested reproduction on its own terms. If the plan adds a condition unsupported by the original task, identify the discrepancy for authorized correction.',
        "must_not": 'Do not read exploration.canvas or substitute a local result for the requested deliverable. Do not add novelty, protected evaluation, optional robustness or other completion requirements. Do not produce a next-step plan.',
        "exit": 'Return passed when the Final Gate is satisfied. For not_passed, classify each concrete unmet requirement in gaps as scientific, delivery or unavailable_material and cite its basis in the original task. Do not output exhaustive requirement ledgers.',
        "procedure": (
            'Use delivery_material_access for the frozen delivery revision. The shared material index gives same-invocation provenance, not per-Finding support. Recheck each still-applicable gap in the earlier not_passed rationale against the actual delivery.',
            'If completion requires protected evaluation, check evaluation_history and primary provenance. A missing ledger record cannot certify an untouched set.',
        ),
    },
    ('reporter', 'report'): {
        "objective": 'Explain this Cycle’s adjudicated scientific result to a researcher.',
        "may_do": 'Use the supplied hypothesis, judge_decision, scientific_synthesis, scientific_model_summary and evidence_scope_digest. Read the report-writing skill for narrative structure, figures and supplementary documents.',
        "must_not": 'Do not infer new results from raw data, assess whole-Goal completion or propose the next frontier. Do not write a reproduction index. The SDK appends it and the original Judge record.',
        "exit": 'Return a scientific title, summary with three ordered sections, and graph_summary as the single overview. The SDK shares this overview with the graph; do not write a second conclusion summary.',
        "procedure": (
            'Use progressive disclosure with report_materials: list or search registered assets via command_prefix and read relevant pages. Follow next_offset as needed. The index shows availability, not scientific relevance. A self-contained result needs no extra reads.',
        ),
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






def stage_definition_text(role: str, stage: str, context=None) -> str:
    """Render one complete Stage definition, including its embedded procedure."""
    delivery = (context or {}).get("submission_delivery")
    if role == "investigator" and delivery:
        if isinstance(delivery, Mapping) and delivery.get("format") == "python":
            return (f"Stage: {stage}\n"
                    "Current objective: Deliver the declared Python interface from retained scientific results in the original Investigator thread.\n"
                    "May do: Read the frozen callable signature, import allowlist, structural mismatch and retained results. Author and register the supplemental Python source in the current output contract.\n"
                    "Must not: Execute the source, rerun research, overwrite original scientific files, change the frozen H or verdict, create a new Native Goal, or make new scientific claims. Preserve the original cumulative token allowance.\n"
                    "Exit / handoff: Return the existing Investigator contract with the authored artifact and the limits of its retained evidence. This stage checks structure only.\n")
        operator_delivery = (context["submission_delivery"].get("authority")
                             == "operator_delivery_structure_only")
        artifact = ("author a supplemental public-interface deliverable and its technical validation, "
                    "preserving the exact retained method and parameters" if operator_delivery else
                    "author a supplemental JSON artifact and register it using the current output contract")
        return (f"Stage: {stage}\n"
                "Current objective: Correct only the explicit public submission structure from retained scientific results in the original Investigator thread.\n"
                f"May do: Read the explicit delivery gap and frozen retained results; {artifact}.\n"
                "Must not: Reopen completed research, change frozen H or verdict, overwrite original scientific files, create a new Native Goal, or invent missing scientific values. Preserve the original cumulative token allowance.\n"
                "Exit / handoff: Return the existing Investigator contract, identifying the supplemental authored artifact and its source-grounded limits.\n")
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
