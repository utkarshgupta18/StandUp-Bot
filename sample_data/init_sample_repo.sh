#!/usr/bin/env bash
# Recreates the demo git histories for the two sample projects so the git
# ingestor, smoke_test.py and the tests have real commits to work with:
#   sample_data/sample_repo          -> "demo-project"
#   sample_data/second_project/repo  -> "invoice-service"
# The .git folders themselves aren't committed to this repo (nested repos
# don't play well with git), so run this once after cloning.
set -e
BASE="$(cd "$(dirname "$0")" && pwd)"

init_repo() {
  local dir="$1"; shift
  cd "$dir"
  if [ -d .git ]; then
    echo "$dir already has git history, skipping."
    return
  fi
  git init -q
  git config user.email "test@example.com"
  git config user.name "Test User"
  git config commit.gpgsign false
  "$@"
  echo "Git history created for $dir:"
  git log --oneline
}

demo_commits() {
  git add README.md && git commit -q -m "Initial commit: add README"
  git add app/main.py && git commit -q -m "Add FastAPI app skeleton"
  git add app/ingest.py && git commit -q -m "Add doc ingestion stub, pending git ingestion next"
}

invoice_commits() {
  git add README.md && git commit -q -m "Initial commit: invoice service README"
  git add src/invoices.py && git commit -q -m "Generate invoice PDFs from order data"
  git add src/webhooks.py && git commit -q -m "Handle Stripe payment_succeeded webhook"
  git add src/mailer.py && git commit -q -m "WIP: email invoices to customers, blocked on SMTP credentials"
}

init_repo "$BASE/sample_repo" demo_commits
init_repo "$BASE/second_project/repo" invoice_commits
