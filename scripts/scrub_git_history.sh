#!/usr/bin/env bash
#
# Remove leaked secrets and committed build artifacts from this repo's history.
#
# WHY THIS IS NEEDED
#   backend/.env was committed in 5 commits (6e1c1c3 .. 007a863) containing a
#   live Neon PostgreSQL connection string, password included. Deleting the file
#   in a later commit does not remove it — `git log -p` still serves it to
#   anyone who clones.
#
#   Two sets of bulk files are also stranded in the pack: robot-fleet.zip
#   (59 MB) and frontend/robot-fleet-dashboard/.npm-cache (509 blobs, 58 MB of
#   npm tarballs). Both are gitignored and untracked today, but history still
#   carries them — which is why a ~5k-line project clones as ~90 MB.
#
# READ BEFORE RUNNING
#   This rewrites every commit SHA from the first offending commit onward and
#   requires a force-push. Anyone else with a clone must re-clone.
#
#   ROTATE THE NEON PASSWORD FIRST. History rewriting removes the credential
#   from the repo, but it does not un-leak it — assume anyone who cloned or
#   forked already has it. Rotation is the step that actually makes it safe;
#   this script only stops it being handed out again.
#
# USAGE
#   pip install git-filter-repo
#   bash scripts/scrub_git_history.sh
#   # inspect the result, then:
#   git push --force-with-lease origin --all
#   git push --force-with-lease origin --tags

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: working tree has uncommitted changes. Commit or stash first." >&2
    exit 1
fi

if ! python -c "import git_filter_repo" 2>/dev/null && ! command -v git-filter-repo >/dev/null 2>&1; then
    echo "ERROR: git-filter-repo not installed. Run: pip install git-filter-repo" >&2
    exit 1
fi

# ── 1. Full backup ──────────────────────────────────────────────────
# A mirror clone keeps every ref exactly as it is now. If the rewrite goes
# wrong, this is what you restore from:
#   git remote add backup <path> && git fetch backup '+refs/heads/*:refs/remotes/backup/*'
#   git reset --hard backup/<branch>
BACKUP="../robot-fleet-platform-backup-$(date +%Y%m%d-%H%M%S).git"
echo ">> Backing up to $BACKUP"
git clone --mirror . "$BACKUP"
echo ">> Backup complete."

# ── 2. Record the current remote ────────────────────────────────────
# git-filter-repo intentionally drops 'origin' after a rewrite, to stop you
# force-pushing a mangled history over a good remote by reflex. Re-add it
# deliberately at the end.
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || echo '')"

# ── 3. Strip the offending paths from every commit ──────────────────
echo ">> Removing leaked secrets and committed build artifacts from all history"
git filter-repo --force \
    --invert-paths \
    --path backend/.env \
    --path .env \
    --path robot-fleet.zip \
    --path frontend/robot-fleet-dashboard/.npm-cache

# ── 4. Belt and braces: scrub the leaked credential itself ──────────
# Catches any commit that pasted the connection string into a script or note
# rather than into backend/.env.
#
# This pattern is deliberately anchored to the specific leaked Neon user and
# host. A generic postgresql://user:pass@host/db pattern also matches every
# localhost and placeholder URL in the repo — the CI service URL, the conftest
# default, the .env.example template — and rewriting those silently breaks both
# the test suite (conftest requires a database name containing "test") and the
# CI job. Only redact the credential that actually leaked.
LEAKED_DB_USER='neondb_owner'
LEAKED_DB_HOST='ep-sparkling-block-aq8h1exs-pooler\.c-8\.us-east-1\.aws\.neon\.tech'

cat > /tmp/replacements.txt <<EOF
regex:postgresql://${LEAKED_DB_USER}:[^@[:space:]"']+@${LEAKED_DB_HOST}[^[:space:]"']*==>postgresql://REDACTED:REDACTED@REDACTED/neondb
EOF
git filter-repo --force --replace-text /tmp/replacements.txt
rm -f /tmp/replacements.txt

# ── 5. Repack ───────────────────────────────────────────────────────
echo ">> Expiring reflog and repacking"
git reflog expire --expire=now --all
git gc --prune=now --aggressive

if [ -n "$ORIGIN_URL" ]; then
    git remote add origin "$ORIGIN_URL" 2>/dev/null || git remote set-url origin "$ORIGIN_URL"
    echo ">> Restored remote: $ORIGIN_URL"
fi

echo
echo "==================================================================="
echo "Rewrite complete. Repo size now:"
git count-objects -vH | grep size-pack
echo
echo "VERIFY before pushing:"
echo "  # 1. secret + bulk files gone from history (expect empty):"
echo "  git log --all --oneline -- backend/.env robot-fleet.zip \\"
echo "      frontend/robot-fleet-dashboard/.npm-cache"
echo
echo "  # 2. credential gone (this script names the user, so exclude it):"
echo "  git log --all -p -- . ':(exclude)scripts/scrub_git_history.sh' \\"
echo "      | grep -i 'neondb_owner'"
echo
echo "  # 3. harmless localhost/example URLs survived intact (expect matches):"
echo "  grep -r 'fleet_test_db' .github/workflows/ci.yml backend/tests/conftest.py"
echo
echo "  # 4. authorship preserved, so the contribution graph survives:"
echo "  git log --format='%an <%ae>' | sort | uniq -c"
echo
echo "Then push (this overwrites the remote):"
echo "  git push --force-with-lease origin --all"
echo "  git push --force-with-lease origin --tags"
echo
echo "Backup kept at: $BACKUP"
echo "==================================================================="
