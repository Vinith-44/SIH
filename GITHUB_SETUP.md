# GitHub setup: private repo + "my approval before any change" (for Vinith + team)

**Analogy:** the GitHub repo is the team's shared Google Drive folder, but with a gatekeeper. Teammates can *propose* changes (a **pull request**); nothing lands in the main copy until **you click Approve + Merge**.

## Important facts (checked 23 Sep 2026)
- On a **free** GitHub account, a **private** repo can't enforce "approval required" (branch protection/rulesets only work on private repos with **GitHub Pro/Team**).
- **GitHub Student Developer Pack gives GitHub Pro free** while you're a student → apply now (BVRIT email or ID card): https://education.github.com/pack. Approval can take a few days.
- On a personal private repo, collaborators always get **write** access (no read-only option). The approval gate comes from branch protection, which needs Pro. Until Pro is active, the team follows the rule "never push to `main`, always open a pull request".
- GitHub blocks single files > 100 MB. **Never commit videos** (privacy + size).

---

## Option A (easiest): let Claude Code do it (after its current run finishes)

1. Install the GitHub CLI once (PowerShell): `winget install --id GitHub.cli`
2. Log in once (a browser window opens): `gh auth login` → GitHub.com → HTTPS → login with browser.
3. Paste this to Claude Code:

```
Set up GitHub for this project. My GitHub username is Vinith-44.
1. Make C:\SIH the single git repository root. If storemind/ already has its own .git, preserve its history if it's easy (e.g. git subtree / filter-repo into storemind/); otherwise re-init and note it in WORK_LOG.md. There must be no nested .git folders.
2. Create a .gitignore: .venv/, __pycache__/, *.db, *.sqlite, videos/** (but keep videos/README.md and videos/GROUND_TRUTH_TEMPLATES/), *.mp4, *.avi, *.mpg, *.mkv, snapshots/, calibration_snapshot*.jpg, models/*.pt, models/*.onnx, models/*.tflite (except small ones we ship on purpose — list them), .env, secrets, "Claude outputs/". Check that no file > 50 MB is staged; list the biggest 20 files before the first commit.
3. Add .github/CODEOWNERS containing: * @Vinith-44
4. Add .github/pull_request_template.md (what changed, how tested, screenshots, checklist: no videos/faces, tests pass).
5. Add CONTRIBUTING.md for teammates on Windows using GitHub Desktop (clone, new branch named <name>/<feature>, commit, push, open pull request; never push to main; how to pull latest).
6. git add, commit "Initial import: research, legacy code, StoreMind pipeline".
7. Create a PRIVATE repo and push: gh repo create StoreMind-SIH26179 --private --source . --remote origin --push
8. Invite collaborators (I'll list usernames): for each USER run gh api -X PUT repos/Vinith-44/StoreMind-SIH26179/collaborators/USER
9. Try to enable branch protection on main with the JSON below (gh api -X PUT repos/Vinith-44/StoreMind-SIH26179/branches/main/protection --input protection.json). If GitHub refuses because the account isn't Pro yet, log "pending GitHub Student Pack / Pro" in HANDOFF_FOR_CLAUDE.md and tell me.
10. From now on, work on a branch named claude/<milestone> and merge to main only via a pull request I approve (I am the admin; I can merge my own).
Teammate usernames: <USERNAME1>, <USERNAME2>, <USERNAME3>, <USERNAME4>, <USERNAME5>
```

`protection.json` (Claude Code creates it):
```json
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```
`enforce_admins: false` → you (admin) can still push/merge yourself; teammates need your approval.

---

## Option B: do it by hand (GitHub website + GitHub Desktop)

1. github.com → **New repository** → name `StoreMind-SIH26179` → **Private** → Create (don't add a README).
2. Install **GitHub Desktop** → File → **Add local repository** → `C:\SIH` → "create a repository" if asked → make sure the `.gitignore` above exists → Commit → **Publish repository** (keep "private" ticked).
3. Repo → **Settings → Collaborators → Add people** → type each teammate's username → they accept the email invite.
4. After GitHub Pro (Student Pack) is active: **Settings → Branches → Add branch protection rule** → Branch name `main` → ✅ Require a pull request before merging → ✅ Require approvals (1) → ✅ Require review from Code Owners → ✅ Dismiss stale approvals → Save.

---

## For teammates (Windows): 5 steps, no command line

1. Accept the invite email from GitHub.
2. Install **GitHub Desktop** → sign in → **File → Clone repository** → `Vinith-44/StoreMind-SIH26179` → choose a folder.
3. **Branch → New branch** → name it `yourname/what-you-do` (e.g. `ravi/stm32-hx711`).
4. Make changes → in GitHub Desktop write a summary → **Commit** → **Push origin** → **Create Pull Request** (opens the browser) → describe what you did → Create.
5. Vinith reviews → approves → merges. To get everyone's latest work: switch to `main` → **Fetch origin → Pull**.

Rules: never commit videos, photos of people, passwords or API tokens. Big datasets stay on Kaggle / Google Drive; put the link in `videos/SOURCES.md`.

## Sources
- Protected branches plan availability: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- Personal repo collaborator permissions (write only): https://docs.github.com/en/account-and-profile/reference/permission-levels-for-a-personal-account-repository
- Free plan private repos can't enforce rulesets: https://github.com/orgs/community/discussions/190190
- Student Pack (free GitHub Pro): https://education.github.com/pack
