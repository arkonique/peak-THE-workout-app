param(
    [Parameter(Position = 0, Mandatory = $true)]
    [string]$Message,

    [Parameter(Position = 1)]
    [string]$Branch = ""
)

$ErrorActionPreference = "Stop"

if (-not $Branch) {
    $Branch = (git branch --show-current).Trim()
}

if (-not $Branch) {
    throw "No current branch is checked out. Checkout a branch or pass -Branch <name>."
}

Write-Host "Staging all changes..."
git add -A

if (git diff --cached --quiet) {
    Write-Host "No staged changes found. Skipping commit and push."
    exit 0
}

Write-Host "Committing with message: $Message"
git commit -m $Message

Write-Host "Pushing branch '$Branch' to origin and heroku..."
git push origin $Branch
git push heroku $Branch
Write-Host "Done."