# Contributing

Hackathon rules of the road. Full details: [docs/08-setup-and-workflow.md](docs/08-setup-and-workflow.md).

1. Read [docs/00-start-here.md](docs/00-start-here.md) first.
2. Work on a task ID from [docs/07-team-and-tasks.md](docs/07-team-and-tasks.md). Only edit files your task owns.
3. Branch `<task-id>-<slug>`, commit `<task-id>: <what>`, `git pull --rebase` before pushing.
4. `boomerang/models.py` is the contract: changes need the tech lead's OK.
5. Never commit `.env` or API keys. Check `git diff --staged` before committing.
6. `main` must stay runnable.
