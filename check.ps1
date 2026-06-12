# Full local verification gate: unit tests, lint, and strict type check.
# Usage: ./check.ps1
$ErrorActionPreference = "Stop"

python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m mypy src tests
exit $LASTEXITCODE
