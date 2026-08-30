#!/usr/bin/env bash
# Recreates the demo git history for sample_data/sample_repo so the git
# ingestor and smoke_test.py have real commits to work with. The .git
# folder itself isn't committed to this repo (nested repos don't play well
# with git), so run this once after cloning.
set -e
cd "$(dirname "$0")/sample_repo"

if [ -d .git ]; then
  echo "sample_repo already has git history, skipping."
  exit 0
fi

git init -q
git config user.email "test@example.com"
git config user.name "Test User"

git add README.md && git commit -q -m "Initial commit: add README"
git add app/main.py && git commit -q -m "Add FastAPI app skeleton"
git add app/ingest.py && git commit -q -m "Add doc ingestion stub, pending git ingestion next"

echo "Sample repo git history created:"
git log --oneline
