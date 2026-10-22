# Sciver model profiles

The documented command is `sciver`; the legacy `theory_master` entry point is
retained for compatibility. The internal Python package stays `theory_master`.

These TOML files are the **bundled default** role-model presets. The standard provider for new installations is Codex (`CodexHTTPS`), not any private third-party account. Existing operators may retain their separately configured legacy provider integration.

- **Tier 1 — `test.toml`**: every Agent uses `gpt-6-luna` / `medium`; intended for workflow testing and smoke runs.
- **Tier 2 — `codex.toml`**: Research Lead uses `gpt-6-sol` / `high`; Investigator uses `gpt-6-sol` / `low`; Challenger + Judge use `gpt-6-sol` / `medium`; Literature + Reporter use `gpt-6-luna` / `low`.
- **Tier 3 — `advanced.toml`**: Research Lead + Investigator use `gpt-6-sol` / `xhigh`; Challenger + Judge use `gpt-6-sol` / `high`; Literature + Reporter use `gpt-6-luna` / `medium`.


Switch with one command:

```bash
sciver model-profile 1   # test
sciver model-profile 2   # codex
sciver model-profile 3   # advanced
```


Inspect the active preset with:

```bash
sciver model-profile status
```

`--model`, `--route`, and the legacy provider override remain explicit run-time overrides; they do not modify these preset files.

## Personal, persistent profile editor

In the Task Console, click the six-stage MODEL tier badge, then **⚙ 设置** to edit all six models and reasoning efforts for any Codex tier. Save writes an atomic private file under `~/.config/theory-master/codex_model_presets.json` (override with `THEORYMASTER_MODEL_PRESETS_FILE`), not the public TOMLs; every Worker selecting that tier reads the updated preset on its next safe invocation in a newly loaded runner. This is a **global per-operating-system-user template**, separate from one Worker's tier selection and from individual Worker route overrides. Legacy private-provider tiers are not editable by this Codex template editor.

**检查 Codex 模型** reads the authenticated account's app-server `model/list` directory without initiating any model turn. A model missing from the complete directory (or an effort omitted for that model) gets a warning; a listed model is only **advertised**, not proven executable. Runtime model invocation errors remain authoritative. When no account login or model catalog is accessible, status remains *unknown* instead of being fabricated as supported.

**Model availability belongs to the selected account, not to the Tier-2 preset.**
If an account/provider rejects `gpt-6-sol`, the SDK preserves the provider error.
An authorized operator can explicitly select another task route; quota switching
never silently rewrites the Tier-2 model or reasoning effort.
Account quota auto-selection cannot silently lower the model or effort.
