#!/bin/sh
# Use a dedicated ASV mirror. Dry runs never refresh it or contact a service.
set -eu

dry_run=false
mirror=${ASV_MIRROR:-"${HOME}/.local/share/asv-qdrant/asv-mirror"}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        --mirror) [ "$#" -ge 2 ] || exit 2; mirror=$2; shift 2 ;;
        *) echo 'Usage: daily-update.sh [--dry-run] [--mirror PATH]' >&2; exit 2 ;;
    esac
done

if [ "$dry_run" = true ]; then
    # The CLI selects and chunks tracked notes, with no client or model.
    exec asv-qdrant ingest "$mirror" --dry-run
fi

# Values are never printed. Embedding uses the existing local fastembed model;
# there is no embedding API key or new credential-loading mechanism.
: "${QDRANT_URL:?QDRANT_URL is required (HTTPS with explicit :443)}"
: "${QDRANT_API_KEY:?QDRANT_API_KEY is required}"
case "$QDRANT_URL" in
    https://*:443|https://*:443/) ;;
    *) echo 'QDRANT_URL must use HTTPS with explicit :443' >&2; exit 2 ;;
esac
if [ ! -e "$mirror" ]; then
    mkdir -p "$(dirname "$mirror")"
    git clone -q --depth 1 --single-branch --branch main \
        https://github.com/SarutobiSasuke8/agentic-satellite-vault.git "$mirror"
fi
[ "$(git -C "$mirror" branch --show-current)" = main ] || {
    echo 'Mirror must be on main' >&2; exit 1;
}
[ -z "$(git -C "$mirror" status --porcelain)" ] || {
    echo 'Mirror has local changes; refusing refresh' >&2; exit 1;
}
git -C "$mirror" fetch -q origin main
git -C "$mirror" merge -q --ff-only FETCH_HEAD
git -C "$mirror" rev-parse HEAD
asv-qdrant ingest "$mirror"
asv-qdrant status
