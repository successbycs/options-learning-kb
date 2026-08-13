# Options Learning KB T480 adapter

This is a read-only port of the governed T480 adapter from `cs-ai-lab-infra`. It uses the same Windows PowerShell → Windows OpenSSH → Ubuntu WSL transport and the same key-authentication/strict-host-key requirements, but carries a smaller KB-focused fixed operation catalog.

It is not a shell. It accepts only the operation IDs in [command-catalog.json](command-catalog.json), does not accept command arguments, does not print or retain credentials, and stores only hashes/metadata of command output in ignored `.t480-execution.local.jsonl`.

Configure the private target outside Git:

```bash
printf 'T480_SSH_TARGET=<your-existing-private-ssh-alias>\n' > .env.t480.local
chmod 600 .env.t480.local
python3 scripts/t480_adapter.py preflight
python3 scripts/t480_adapter.py execute --operation options_kb_preflight
python3 scripts/t480_adapter.py execute --operation ollama_bge_status
```

For the existing shared lab on this workstation, the adapter also reads that repository's already-ignored `.env.t480.local` as a fallback. It never copies, prints, or commits the target.

The adapter checks the shared `cs-ai-lab_internal` Docker network, the existing PostgreSQL + pgvector service, and private Ollama `bge-m3`. It intentionally does not apply KB migrations, start services, upload transcripts, inspect database content, or access broker/trading systems. Those actions retain their own review and explicit approval paths.
