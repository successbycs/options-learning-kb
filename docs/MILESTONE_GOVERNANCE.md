# Evidence-first delivery governance

The KB uses a small, executable milestone controller. A static JSON status, passing unit tests, an agent statement, or an operator note cannot complete a milestone by itself.

The only completion path is:

```text
not_started → in_progress → declared capture → hash/freshness/marker verification
→ independent read-back → complete
```

`blocked` and `needs_fix` require a concrete reason. There is no direct `not_started → complete` transition and the tracked registry rejects a milestone declared complete.

## Evidence handling

The tracked [registry](../milestones/registry.json) declares each real surface, fixed capture command, required markers, freshness limit, and verifier. Runtime state and raw evidence are retained only under ignored `data/milestone-*` paths. The controller records their SHA-256 and rejects changed, stale, missing, or out-of-tree evidence.

The M1 verifier does more than reread its capture: it calls the fixed T480 adapter again to independently confirm the existing PostgreSQL/pgvector, `cs-ai-lab_internal` network, and local `bge-m3` service. It makes no mutations.

## Commands

```bash
python3 scripts/kb_milestones.py validate-registry
python3 scripts/kb_milestones.py status
python3 scripts/kb_milestones.py start --id KB-M0
python3 scripts/kb_milestones.py capture --id KB-M0
python3 scripts/kb_milestones.py verify --id KB-M0
python3 scripts/kb_milestones.py complete --id KB-M0
python3 scripts/kb_milestones.py block --id KB-M2 --reason 'Awaiting approved migration window.'
```

Only KB-M0 and KB-M1 currently have implemented capture/verifier probes. KB-M2 onward deliberately remain non-completable until their real deployment, transcript, QA, and integration read-backs are implemented. This is a guardrail, not a missing status update.

## Delivery guardrails

- Do not apply the database migration, deploy containers, or ingest a transcript merely to produce evidence; those actions need their existing explicit operator approval and permission basis.
- Do not place course text, raw T480 evidence, private network details, credentials, or evidence state in Git.
- Do not mark a KB result as evidence of trading edge or use it to alter risk policy, approval, broker access, or trade execution.
- KB-M5 remains blocked until the Options Decision Agent and KB implement one token-authenticated, versioned, mutually tested read-only retrieval contract.
