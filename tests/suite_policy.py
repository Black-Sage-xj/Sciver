from __future__ import annotations

from pathlib import Path

CATEGORIES = ("invariant", "architecture", "regression", "ui")

# Primary ownership category for every test module. Categories describe why a test
# exists, not how important it is. A small number of tests in mixed legacy modules
# may additionally participate in the invariant gate via INVARIANT_TEST_IDS.
MODULE_CATEGORIES: dict[str, str] = {
    "test_budget_review_original_dialogue_20261011": "invariant",
    "test_compute_dispatch_parallel": "regression",
    "test_cycle_authority_and_multi_source_20261009": "invariant",
    "test_cycle_replay_checkpoint": "invariant",
    "test_decisive_test_inferential_quality_prompt_20261011": "architecture",
    "test_direct_investigator_handoff_20261009": "architecture",
    "test_hypothesis_dossier_canvas_20261009": "architecture",
    "test_investigator_complete_handoff_prompt_20261009": "architecture",
    "test_investigator_native_goal_20261010": "architecture",
    "test_native_goal_live_budget_20261011": "invariant",
    "test_native_goal_quota_auto_adjust_20261010": "regression",
    "test_operator_control": "ui",
    "test_preferred_focus_important_events": "invariant",
    "test_scientific_resource_feasibility_prompt_20261011": "architecture",
    "test_web_operator_preferences": "ui",
    "test_model_route_preflight_20261011": "ui",
    "test_web_role_routes": "ui",
    "test_web_tui_bridge": "ui",
    "test_test_runner_diagnostics": "invariant",
    "test_scientific_canary_reference": "regression",
    "test_budget_snapshot_sync_20261009": "invariant",
    "test_research_plan_v4_migration_20261009": "invariant",
    "test_tma_special_samples_20261009": "regression",
    "test_external_canvas_nodes": "architecture",
    "test_internal_attention_review": "invariant",
    "test_judge_rework_default_three_20261009": "regression",
    "test_judge_rework_scientific_closure_20261009": "regression",
    "test_runtime_registry_rebind": "regression",
    "test_vault_data_isolation": "invariant",
    "test_5cycle_audit_fix_loop": "regression",
    "test_abc_pipeline": "architecture",
    "test_architecture_invariants": "invariant",
    "test_attention_takeover": "ui",
    "test_audit_queue_cli": "invariant",
    "test_audit_regressions_20260928": "regression",
    "test_backend_turn_completion": "architecture",
    "test_cli_ls_limit": "ui",
    "test_codex_events": "ui",
    "test_codex_accounts": "architecture",
    "test_investigator_round_semantics": "architecture",
    "test_investigator_budget_review": "invariant",
    "test_investigator_submission_retirement": "architecture",
    "test_investigator_source_boundary": "architecture",
    "test_contract_refactor_baseline": "regression",
    "test_contracts": "architecture",
    "test_cross_cycle_evidence_continuity": "architecture",
    "test_core": "architecture",
    "test_repair_verification_state_machine_20261022": "architecture",
    "test_cycle_anomalies": "regression",
    "test_costs": "ui",
    "test_cycle_audit_relay": "regression",
    "test_cycle_git_commit": "invariant",
    "test_error_relay": "invariant",
    "test_supervisor_incident_session": "regression",
    "test_supervisor_intervention": "invariant",
    "test_evidence": "invariant",
    "test_embedded_state": "invariant",
    "test_foreground_cli": "ui",
    "test_frontier_status_authority": "invariant",
    "test_goal_authority_sqlite": "invariant",
    "test_goal_completion_gate": "architecture",
    "test_identity_contract_consistency": "invariant",
    "test_invocations": "invariant",
    "test_judge_rework_authorization": "regression",
    "test_lightweight_judge_architecture": "invariant",
    "test_load_snapshots": "invariant",
    "test_observability": "ui",
    "test_operational_guardrails": "invariant",
    "test_output_normalization": "regression",
    "test_pricing": "ui",
    "test_provider_environment_boundary": "architecture",
    "test_provider_quota_pause": "regression",
    "test_read_trace": "invariant",
    "test_recovery_resume": "invariant",
    "test_regression_cases": "regression",
    "test_refactor_guardrails": "architecture",
    "test_research_reading": "architecture",
    "test_runtime_manager": "invariant",
    "test_scientific_model": "architecture",
    "test_skills": "architecture",
    "test_stage_prompts_and_repair": "architecture",
    "test_static_review_fixes_20261008": "invariant",
    "test_suite_policy": "invariant",
    "test_system_config_migration": "architecture",
    "test_tma_0082_runtime_guardrails": "invariant",
    "test_task_mode": "architecture",
    "test_task88_regressions": "regression",
    "test_terminal_assessment_contract_versions": "architecture",
    "test_terminal_assessment_prompt": "architecture",
    "test_transaction_integrity": "invariant",
    "test_abandon_cycle_20261012": "regression",
    "test_agent_graph_view": "architecture",
    "test_budget_allowance_basis_20261012": "invariant",
    "test_budget_reject_reference_repair_20261019": "invariant",
    "test_budget_reviewer_trace_archive": "invariant",
    "test_canvas_docs_current_20261018": "architecture",
    "test_capacity_thread_busy_recovery_20261013": "regression",
    "test_change_register_cli": "invariant",
    "test_codex_provider_400_recovery_20261012": "regression",
    "test_cycle_budget_internal_continue": "invariant",
    "test_cycle_outcome_documents": "architecture",
    "test_embedded_preserve_outcome_20261019": "invariant",
    "test_frontier_reconciliation": "invariant",
    "test_hot_role_route_20261012": "architecture",
    "test_hypothesis_display_contract": "architecture",
    "test_hypothesis_dossier_migration": "architecture",
    "test_incident_chat_backend_compat": "regression",
    "test_incident_supervisor_dispatch_20261018": "regression",
    "test_incident_supervisor_write_authority_20261018": "invariant",
    "test_investigator_finding_self_contained_20261011": "architecture",
    "test_investigator_historical_reuse_20261022": "architecture",
    "test_investigator_prompt_consistency_20261022": "architecture",
    "test_investigator_minimal_findings_20261012": "architecture",
    "test_judge_literature_reporter_prompt_20261012": "architecture",
    "test_kill_cycle_20261012": "invariant",
    "test_lead_challenger_prompt_unification_20261012": "architecture",
    "test_multi_worker": "regression",
    "test_native_goal_blocker_reason_20261013": "architecture",
    "test_native_goal_length_budget_20261012": "architecture",
    "test_preservation_failed_replay_cycle92": "regression",
    "test_preserve_budget_reject_release_20261019": "invariant",
    "test_preserve_web_control_failure_20261019": "ui",
    "test_provenance_authority_boundary": "architecture",
    "test_provider_capacity_reconnect_20261012": "regression",
    "test_quota_auto_adjust_threshold": "regression",
    "test_quota_failover_policy": "regression",
    "test_rework_events_on_resume_20261012": "invariant",
    "test_scheduler_account_switch_gap_20261019": "regression",
    "test_science_canvas_export": "architecture",
    "test_science_frontier_v2": "architecture",
    "test_science_graph_preserve_legacy": "architecture",
    "test_science_graph_store": "architecture",
    "test_science_graph_tools": "architecture",
    "test_submodule_task_discovery": "regression",
    "test_supervisor_blocked_preservation_20261013": "invariant",
    "test_supervisor_capacity_idle_20261019": "regression",
    "test_supervisor_reliability_20261013": "invariant",
    "test_supervisor_runtime_repair_20261014": "regression",
    "test_supervisor_scientific_authority": "invariant",
    "test_terminal_capacity_turn_settlement_20261019": "regression",
    "test_test0_goal_scoring_boundary": "regression",
    "test_tui_process_paused_lifecycle_20261012": "ui",
    "test_unified_supervisor_continuity_20261019": "regression",
    "test_worker_account_switch_priority_20261018": "regression",
    "test_worker_kill_request_20261018": "invariant",
    "test_worker_migration": "regression",
    "test_worker_mode": "architecture",
    "test_worker_model_profile_editor_20261018": "ui",
    "test_worker_observer_scope_recovery_20261019": "regression",
    "test_worker_per_worker_cycle_quota_20261018": "regression",
    "test_worker_release_cycle_reconciliation_20261018": "invariant",
    "test_worker_runtime": "regression",
    "test_worker_status_read_model_20261017": "ui",
    "test_tm_scholar_native": "architecture",
    "test_tui": "ui",
    "test_global_goal_terminal": "invariant",
    "test_goal_advancement_repair_audit_20261020": "regression",
    "test_mw_a_goal_seal_store": "invariant",
    "test_mw_a_goal_completion_path": "invariant",
    "test_mw_b_cycle_claims": "invariant",
    "test_mw_b_proposal_gate": "invariant",
    "test_mw_b_migration": "regression",
    "test_mw_c_cycle_claim_scheduler": "invariant",
    "test_mw_c_run_once_and_recovery": "invariant",
    "test_mw_e_canvas_science_writes": "invariant",
    "test_mw_e_scientific_model_pending": "invariant",
    "test_mw_d_proposal_prompt": "architecture",
    "test_budget_failure_reconciliation_20261022": "regression",
    "test_budget_review_recovery_loop_guard_20261022": "regression",
    "test_budget_three_plus_three_20261021": "regression",
    "test_candidate_prelock_feasibility_20261022": "architecture",
    "test_identity_layer_optimization": "architecture",
    "test_input_limits_handoff_20261023": "architecture",
    "test_input_qualification_evidence_20261022": "architecture",
    "test_joint_input_comparison_20261022": "architecture",
    "test_lead_history_question_scope_20261023": "architecture",
    "test_preservation_workspace_subpaths_20261022": "regression",
    "test_public_release_guard_20261022": "architecture",
    "test_quota_watcher_legacy_preserve_20261022": "regression",
    "test_research_plan_history": "regression",
    "test_scheduler_recovery_reconciliation_20261022": "regression",
    "test_science_graph_compact_v3": "regression",
    "test_science_graph_context_provenance": "regression",
    "test_scientific_qualification_20261022": "architecture",
    "test_sciver_branding": "architecture",
    "test_sciver_public_docs": "architecture",
    "test_supervisor_parallel_budget_orphan_20261022": "regression",
    "test_supervisor_recovery_owner_20261022": "regression",
    "test_supervisor_repair_receipts_20261022": "regression",
    "test_supervisor_validator_20261022": "regression",
    "test_worker_account_preferences_20261022": "regression",
    "test_worker_verified_quota_wait_20261022": "regression",
}

# test_core.py is intentionally not split in this refactor because it already has
# unrelated worktree edits and a large move would create noisy risk. These stable
# system guarantees still participate in the invariant gate even though the module
# remains architecture-owned.
INVARIANT_TEST_IDS = {
    "test_core.TheoryMasterTest.test_task_ids_allocate_compactly_and_duplicate_ids_fail_closed",
    "test_core.TheoryMasterTest.test_process_control_commands_are_declined",
    "test_core.TheoryMasterTest.test_history_rewriting_commands_are_declined",
    "test_core.TheoryMasterTest.test_writes_outside_cycle_workspace_are_declined",
    "test_core.TheoryMasterTest.test_locked_criteria_and_budget_reach_downstream_roles",
    "test_core.TheoryMasterTest.test_locked_candidate_is_the_investigated_branch",
    "test_core.TheoryMasterTest.test_role_started_persists_thread_before_invoke",
    "test_core.TheoryMasterTest.test_recovery_resume_reuses_cycle_and_skips_completed_roles",
    "test_core.TheoryMasterTest.test_recovery_resume_continues_remaining_cycle_budget",
    "test_core.TheoryMasterTest.test_recovery_control_pause_is_persisted",
    "test_core.TheoryMasterTest.test_recovery_control_rejects_stopped_cycle",
    "test_core.TheoryMasterTest.test_final_canvas_projection_failure_persists_resumable_authority_state",
    "test_core.TheoryMasterTest.test_write_roles_do_not_pass_untrusted_approval_policy",
}


def module_name_from_test_id(test_id: str) -> str:
    return test_id.split(".", 1)[0]


def categories_for_test_id(test_id: str) -> frozenset[str]:
    module_name = module_name_from_test_id(test_id)
    primary = MODULE_CATEGORIES.get(module_name)
    categories = {primary} if primary else set()
    if test_id in INVARIANT_TEST_IDS:
        categories.add("invariant")
    return frozenset(category for category in categories if category)


def test_modules_on_disk(tests_root: Path) -> set[str]:
    return {
        path.stem
        for path in tests_root.glob("test_*.py")
        if path.is_file()
    }


def registry_errors(tests_root: Path) -> list[str]:
    errors: list[str] = []
    on_disk = test_modules_on_disk(tests_root)
    registered = set(MODULE_CATEGORIES)

    missing = sorted(on_disk - registered)
    extra = sorted(registered - on_disk)
    invalid = sorted(
        (module, category)
        for module, category in MODULE_CATEGORIES.items()
        if category not in CATEGORIES
    )
    invalid_overrides = sorted(
        test_id
        for test_id in INVARIANT_TEST_IDS
        if module_name_from_test_id(test_id) not in registered
    )

    if missing:
        errors.append(f"unclassified test modules: {', '.join(missing)}")
    if extra:
        errors.append(f"registered modules missing on disk: {', '.join(extra)}")
    if invalid:
        rendered = ", ".join(f"{module}={category}" for module, category in invalid)
        errors.append(f"invalid categories: {rendered}")
    if invalid_overrides:
        errors.append(
            "invariant overrides reference unregistered modules: "
            + ", ".join(invalid_overrides)
        )
    return errors
