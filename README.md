# asv-qdrant-index

A retrieval index over the engineering notes in the Agentic Satellite Vault (ASV), stored in Qdrant.

The ASV is the source of truth. This index is not a second wiki. Every hit is a pointer to an ASV path at a git blob sha; open the file before acting on it.

Phase 1 covers: create the `asv-engineering` collection, diff-ingest the allowlisted ASV paths, and search from the command line.

## What gets indexed

Tracked Markdown files only (`git ls-files`), matching:

- `Workspace/Grok/Engineering/**/*.md` (design notes)
- `Workspace/Grok/Chief of Staff/Portfolio/Portfolio Map.md`
- `Workspace/Messages/{lingxi-engineer-grok-bot,claude-code,codex,chief-of-staff-grok-bot}/*.md` (tickets, replies, FYIs)

Hard exclusions win over the allowlist: `Context/**`, `gryps-brain`, brand-confidential trees, `automation-schedule.md`. A note is also skipped when its frontmatter has `disclosure: confidential | brand-confidential | internal-only | private` or `confidential: true`, or when its frontmatter is unparsable and mentions a restriction. Any chunk that looks like a credential is dropped before embedding.

## Embedding model

`BAAI/bge-small-en-v1.5` through [fastembed](https://github.com/qdrant/fastembed), 384 dimensions, cosine.

Why this model:

- Same 384-d size and CPU cost as `all-MiniLM-L6-v2`, but clearly stronger on retrieval benchmarks (MTEB retrieval), which is the only job here.
- fastembed runs it as ONNX on CPU with no PyTorch, so it fits a small VPS runner and a laptop alike.
- It is Qdrant's own fastembed default, so the model and client versions are maintained together.
- Queries get the bge retrieval prefix; documents do not, as the model card recommends.

Changing the model changes the vector size. The tool refuses to write to a collection with a different size; create a new collection rather than mixing vectors.

## Point schema

- id: UUID5 of `path#chunk_index`, so re-ingest overwrites in place
- vector: 384-d, cosine
- payload: `path`, `title`, `updated`, `source_sha` (git blob sha of the file), `chunk_index`, `excerpt` (first 300 characters), `asv_head` (ASV commit at ingest)

Chunks split on `#`, `##` and `###` headings, then at 1,500 characters with 200 characters of overlap. Each chunk is embedded with the note title in front.

## Runbook

```bash
python -m venv .venv
.venv/bin/pip install -e .        # Windows: .venv\Scripts\pip
cp .env.example .env              # fill in locally; never commit it
set -a; . ./.env; set +a          # or export the two variables another way
```

Credentials come from `QDRANT_URL` and `QDRANT_API_KEY` only. If either is missing, every command that needs the cluster exits with `QDRANT_URL is not set` or `QDRANT_API_KEY is not set`.

```bash
asv-qdrant create                                   # idempotent
asv-qdrant ingest /path/to/Agentic-Satellite-Vault  # diff: only changed files are re-embedded
asv-qdrant ingest /path/to/asv --full               # re-embed everything
asv-qdrant ingest /path/to/asv --dry-run            # select and chunk, no cluster needed
asv-qdrant search "what blocked the self-hosted runner install" -k 5
asv-qdrant smoke /path/to/asv                       # ingest, then prove the Qdrant design note is in the top 5
```

Add `--local` before the subcommand to run against an in-memory Qdrant with no credentials, for example `asv-qdrant --local smoke /path/to/asv`. Nothing persists.

Diff ingest: a file is re-embedded when its blob sha differs from the one stored; its old points are deleted first so a shorter file leaves no stale chunks. Files that left the allowlist or the repo are deleted from the collection.

Pull the ASV (`git pull --ff-only`) before ingesting; the index reflects whatever is checked out.

## Out of scope for Phase 1

SEO or AEO corpus, cross-brand memory, an MCP server over the index, auto-embed on every ASV write, editing or deleting ASV content, `Context/Internal` and brand-confidential packs.

## Licence

MIT
