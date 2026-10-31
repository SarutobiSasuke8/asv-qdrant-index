# asv-qdrant-index

A retrieval index over the engineering notes in the Agentic Satellite Vault (ASV), stored in Qdrant.

The ASV is the source of truth. This index is not a second wiki. Every hit is a pointer to an ASV path at a git blob sha; open the file before acting on it.

- **Phase 1:** create the `asv-engineering` collection, diff-ingest the allowlisted ASV paths, search from the command line.
- **Phase 2:** note types, sender and date filters, routine-log noise cut, a read-only MCP server, a known-answer eval set, and a daily update on Windows.

## What gets indexed

Tracked Markdown files only (`git ls-files`), matching:

- `Workspace/Grok/Engineering/**/*.md` (design notes)
- `Workspace/Grok/Chief of Staff/Portfolio/Portfolio Map.md`
- `Workspace/Messages/{lingxi-engineer-grok-bot,claude-code,codex,chief-of-staff-grok-bot}/*.md` (tickets, replies, FYIs)

Hard exclusions win over the allowlist: `Context/**`, `gryps-brain`, brand-confidential trees, `automation-schedule.md`. A note is also skipped when its frontmatter has `disclosure: confidential | brand-confidential | internal-only | private` or `confidential: true`, or when its frontmatter is unparsable and mentions a restriction. Any chunk that looks like a credential is dropped before embedding.

Routine machine output is left out by file name, because it drowns out decisions: intake logs, daily digests, branch boards, inbox checks, automation runs, operating briefs, usage logs and the daily `mcp-downloads-*` scans. The list is `NOISE` in `asv_qdrant/core.py`.

## Note types and filters

Each point carries `kind`, `sender` and `date`:

| kind | from |
|---|---|
| `design-note` | anything under `Workspace/Grok/Engineering/` |
| `portfolio` | the Portfolio Map |
| `blocker`, `fyi`, `reply`, `ticket`, `claim` | the message file name (`completion` counts as `reply`, `request` as `ticket`), then frontmatter `type: ticket` |
| `message` | everything else |

`sender` is frontmatter `from:` in lower case. `date` is the `YYYY-MM-DD` prefix of the file name, else frontmatter `date`, `created` or `updated`.

```bash
asv-qdrant search "why did the runner install stall" --kind ticket --kind reply --since 2026-10-25
asv-qdrant search "coder ticket limits" --kind design-note
asv-qdrant search "restack" --sender lingxi-engineer-grok-bot
```

## MCP server

`asv-qdrant-mcp` is a read-only stdio MCP server with two tools:

- `search_asv_engineering(query, k=5, kind=None, sender=None, since=None)`: hits with score, path, title, kind, sender, date, source sha and excerpt
- `asv_index_status()`: point count and the ASV commits the index was built from, to judge freshness

Both tools are annotated read-only and non-destructive. Neither writes to Qdrant or the ASV.

Register it with Claude Code for every project:

```bash
claude mcp add --scope user asv-engineering-memory -- /path/to/asv-qdrant-index/.venv/Scripts/asv-qdrant-mcp.exe
```

Do not pass the key with `-e`: that stores it in plain text in the MCP config. The server reads `QDRANT_URL` and `QDRANT_API_KEY` from its own environment and, on Windows, falls back to the user environment store, because MCP hosts often start servers with a trimmed environment.

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
- payload: `path`, `title`, `updated`, `source_sha` (git blob sha of the file), `chunk_index`, `excerpt` (first 300 characters), `kind`, `sender`, `date`, `date_int`, `schema`, `asv_head` (ASV commit at ingest)
- payload indexes: `path`, `kind`, `sender` (keyword), `date_int` (integer)

Chunks split on `#`, `##` and `###` headings, then at 1,500 characters with 200 characters of overlap. Each chunk is embedded with the note title in front.

`SCHEMA` in `core.py` is the payload version. Bump it when payload fields change and the next ingest rewrites every file.

## Runbook

```bash
python -m venv .venv
.venv/bin/pip install -e .        # Windows: .venv\Scripts\pip
cp .env.example .env              # fill in locally; never commit it
set -a; . ./.env; set +a          # or export the two variables another way
```

Credentials come from `QDRANT_URL` and `QDRANT_API_KEY` only. For Qdrant Cloud, if the client fails with an SSL EOF on the default REST port 6333 (some networks block it), add `:443` to the end of `QDRANT_URL`. If either is missing, every command that needs the cluster exits with `QDRANT_URL is not set` or `QDRANT_API_KEY is not set`.

```bash
asv-qdrant create                                   # idempotent; also adds missing payload indexes
asv-qdrant ingest /path/to/Agentic-Satellite-Vault  # diff: only changed files are re-embedded
asv-qdrant ingest /path/to/asv --full               # re-embed everything
asv-qdrant ingest /path/to/asv --dry-run            # select and chunk, no cluster needed
asv-qdrant search "what blocked the self-hosted runner install" -k 5
asv-qdrant status                                   # point count and source ASV commits
asv-qdrant eval                                     # known-answer questions, hit rate in the top 5
asv-qdrant smoke /path/to/asv                       # ingest, then prove the Qdrant design note is in the top 5
```

`--only '<glob>'` limits `ingest` or `smoke` to one path glob, for example `--only 'Workspace/Grok/Engineering/*'`. A scoped run never deletes points outside its glob.

Cost: on 2026-10-31 the allowlist matched 1,765 files and 4,712 chunks after the noise cut. On a laptop CPU the model embeds about 4 to 5 chunks a second, so a full ingest takes roughly 20 minutes. Points are written in batches of 128 as they are embedded, so an interrupted run keeps its progress. Later runs only re-embed changed files.

Add `--local` before the subcommand to run against an in-memory Qdrant with no credentials, for example `asv-qdrant --local smoke /path/to/asv`. Nothing persists.

Diff ingest: a file is re-embedded when its blob sha or the payload schema differs from what is stored; its old points are deleted first so a shorter file leaves no stale chunks. Files that left the allowlist or the repo are deleted from the collection.

## Daily update (Windows)

`scripts/daily-update.ps1` refreshes a dedicated shallow clone of the ASV at `C:\Dev\Data\asv-mirror` (never your working copy), then runs the diff ingest. It logs counts and the ASV commit to `%LOCALAPPDATA%\asv-qdrant\ingest.log`, never the key.

```powershell
powershell -File scripts\install-daily-task.ps1          # 07:30 daily; catches up at next logon if missed
Start-ScheduledTask -TaskName "ASV Qdrant daily ingest"   # run now
Unregister-ScheduledTask -TaskName "ASV Qdrant daily ingest" -Confirm:$false   # remove
```

07:30 Dublin is before Lingxi's portfolio watch (08:53) and daily assign (about 10:15).

## Evals

`evals/questions.yaml` holds known-answer questions, phrased the way an agent would ask rather than copied from note titles. `asv-qdrant eval` reports which ones find an acceptable path in the top k. Re-run it after changing the scope, the noise list or the model, and add a case whenever a search misses something it should have found.

## Out of scope

SEO or AEO corpus, cross-brand memory, auto-embed on every ASV write, editing or deleting ASV content, `Context/Internal` and brand-confidential packs.

## Licence

MIT
