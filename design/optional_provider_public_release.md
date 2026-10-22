# Optional model providers and public-release boundary

Sciver's public SDK should contain a generic provider contract, **not a
specific operator's model broker, account credentials or quota integration**.

## Runtime extension (operator-owned)

A standard Codex/ChatGPT route needs no extra setup. To add an API-compatible
provider, create `~/.config/theory-master/provider_connections.toml`, mode
`0600`. For an editable development checkout, `config/local/provider_connections.toml`
can be used instead; the entire `config/local/` directory is Git-ignored.

Example (placeholders only):

```toml
[model_providers.ExampleProvider]
base_url = "https://example.invalid/v1"
env_key = "EXAMPLE_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

Set `EXAMPLE_API_KEY` (or `THEORYMASTER_EXAMPLE_API_KEY`) in the
operator's shell or private environment file. Never store tokens in a Git
source tree. Model routes can reference `model@ExampleProvider` once
configured. User-owned profile TOMLs may live under
`config/local/model_profiles/` in development, instead of the package's
public `model_profiles/` directory.

For an existing external configuration file in the shared Codex runtime
home, use `source_file = "example.config.toml"` instead of `base_url`.
Both files must be regular owner-owned, mode-`0600` files. The SDK passes
tokens only in the runtime environment, never in logged SDK overrides.
An unknown provider is rejected until explicitly registered.

**Quota exhaustion is provider-sticky.** Exhaustion of all Codex accounts
pauses the corresponding scientific work until Codex quota is available
again. It is not permission to rotate to any other provider. User-requested
manual Provider selection remains a separate operator decision.

## Publish boundary

Private development ancestry must stay outside public releases. Review both
source files and commit metadata; deleting a file does not erase its history.

Prepare a clean public branch based on a reviewed public tree. Before a
GitHub release or Python upload, run:

```sh
python scripts/public_release_guard.py --ref HEAD
python scripts/public_release_guard.py --ref HEAD --artifact dist/sciver.whl
```

The second command uses the actual wheel filename you built. Run the guard
again on every built wheel and sdist. Any private-provider name, dedicated
profile, dedicated code, credential file or leaked domain **blocks** release.
The tool is a detection gate, **not a sanitizer**; fix the candidate public
tree and rerun it. To make this a preventive GitHub merge gate, require
the corresponding check as a mandatory status check in branch protection.

This arrangement keeps personal adapters out of the published branch rather
than relying on merely hiding web buttons. It does not erase content already
present in historical public Git commits. Remediating public history is a
separate security-sensitive decision.
