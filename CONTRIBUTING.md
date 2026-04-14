# Contributing to AgentOps

We appreciate contributions and suggestions for this project!

## Ways to Contribute

- **Issues:** Report bugs, propose enhancements, or share feature requests.
- **Comments:** Engage in discussions, help others, and review proposals.
- **Documentation:** Improve guides and clarity for new users.
- **Design:** Contribute to open design discussions and new patterns.
- **Tests:** Strengthen reliability through unit and integration tests.
- **Code:** Submit fixes, enhancements, or new modules via pull requests.

## Before You Start

1. Read [`docs/how-it-works.md`](docs/how-it-works.md) — it explains the architecture, directory structure, and data flow.
2. Read the [Quick Reference for New Contributors](docs/how-it-works.md#quick-reference-for-new-contributors) section for a fast onboarding checklist.
3. Set up your local environment:

```bash
# Clone the repository
git clone https://github.com/<your-fork>/agentops.git
cd agentops

# Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# Install in editable mode
pip install -e .
pip install pytest

# Run tests to verify everything works
python -m pytest tests/ -x -q
```

## Contribution Guidelines

To maintain project quality, the following items will be considered during the PR review.

> Adhering to these best practices will streamline the review process.

### General Rules

- **Target the `develop` Branch:** Always direct your pull request to the `develop` branch to ensure that changes are properly integrated into the project's development workflow.

- **Keep Pull Requests Small:** Aim to make your pull requests as focused and concise as possible. This makes it easier to review and ensures quicker integration into the codebase.

- **Associate with Prioritized Issues:** Ensure that each pull request is linked to a specific, prioritized issue in the project backlog. This helps maintain alignment with project goals and ensures that work is being done on tasks of the highest importance.

- **Include Documentation:** Every new feature or functionality must be accompanied by clear documentation explaining its purpose and configuration. This ensures others can use it independently in a self-service manner.

- **Bugs and Documentation Corrections:** Pull requests that address bugs or correct documentation do not need to be associated with prioritized issues. These can be submitted directly to maintain the quality and accuracy of the project.

### Architecture Rules

These rules are critical to maintaining the project's architecture. PRs that violate them will be rejected.

| Rule | Details |
|---|---|
| **Thin CLI** | `cli/app.py` only parses arguments and calls into `services/`. No business logic. |
| **Core is pure** | `core/` must have zero Azure SDK imports and zero network calls. It only transforms data. |
| **Lazy Azure imports** | All `azure-*` SDK imports go inside functions in `backends/` and `services/`, never at module top level. |
| **pathlib.Path only** | No raw string paths. Use `pathlib.Path` everywhere. |
| **No global state** | No singletons, module-level side effects, or hidden shared state. |
| **Pydantic v2 schemas** | Every config file and output file must be validated by a model in `core/models.py`. |
| **Exit codes are sacred** | `0` = pass, `2` = threshold failure, `1` = error. Do not change their meaning. |

### Where to Put New Code

| I want to… | Where it goes |
|---|---|
| Add a new Pydantic model or schema field | `core/models.py` |
| Add a new config file type | `core/config_loader.py` + `core/models.py` |
| Add a new local evaluator | `backends/eval_engine.py` (shared evaluation engine) |
| Add a new execution backend | `backends/` (new file implementing `Backend` protocol) + register in `services/runner.py` |
| Support a new endpoint kind | `core/models.py` (`EndpointKind` literal) + `services/runner.py` (resolution) + `backends/` |
| Add a new CLI command | `cli/app.py` (keep it thin — delegate to `services/`) |
| Add a new workflow/service | `services/` (new file) |
| Add starter templates | `templates/` + update `pyproject.toml` package-data |

### Testing Expectations

- **Unit tests** for all new logic in `tests/unit/`.
- **Integration tests** for end-to-end workflows in `tests/integration/`.
- All Azure SDK calls must be **mocked** — tests must run without Azure credentials.
- Tests must assert correct **exit codes**.
- Run all tests before submitting: `python -m pytest tests/ -x -q`

### Documentation

When contributing to documentation:

- **Focus on Clarity:** Prioritize straightforward language and well-structured content. Documentation should be easy to read.

- **Follow Existing Patterns:** Review existing documentation pages to maintain consistency in style, formatting, and tone.

- **Use AI Tools Wisely:** GitHub Copilot and similar tools can help generate documentation, but always review and refine the output. Avoid excessive use of emojis, dashes, and images. Keep documentation clean, clear, and professional.

## Changelog Convention

We maintain [CHANGELOG.md](CHANGELOG.md) using the **Keep a Changelog** format and **Semantic Versioning**.

Rules:

- All user-visible changes must include a changelog entry under **`[Unreleased]`**.
- Use the standard sections: **Added**, **Changed**, **Fixed** (and **Deprecated/Removed/Security** if needed).
- Write entries as short, imperative bullets (what changed, not how).
- Do not edit or reorder historical release sections after they ship.

Release process (maintainers):

- When cutting a release (e.g. `0.1.0`), move the relevant items from `[Unreleased]` into a new version section like `## [0.1.0] - YYYY-MM-DD`.
- After release, `[Unreleased]` should be left ready for new entries.

## Code Update Workflow

We use a simplified version of the [Fork and Branch Workflow](https://blog.scottlowe.org/2015/01/27/using-fork-branch-git-workflow/) alongside [Git Flow](https://nvie.com/posts/a-successful-git-branching-model/) for branching strategy. The `main` branch always contains deployment-ready code, while the `develop` branch serves as the integration branch.

Contributors create feature branches from `develop` in their forks. Once changes are completed, they submit a pull request to the `develop` branch in the upstream repository. After review and approval, reviewers merge the changes into `develop`. Periodically, maintainers group these changes into a pull request from `develop` to `main` for final review and merging.

### Process Overview

1. **Fork the Repository** — Create a copy of the upstream repository under your own GitHub account.

2. **Clone Locally** — Download your forked repository to your local machine.

3. **Add Upstream** — Link the original upstream repository as `upstream` to keep your fork synchronized.

4. **Create a Feature Branch** — From your fork's `develop` branch, create a feature branch for your change (e.g., `feature/new-evaluator`).

5. **Commit and Push Changes** — Implement your updates locally, commit, and push them to your fork on GitHub.

6. **Open a Pull Request to `develop`** — Open a PR from your feature branch in your fork to the upstream repository's `develop` branch.

7. **Sync with Upstream `develop`** — After your PR is merged, update your fork's `develop` branch with the latest changes from the upstream.

8. **Create a Release Branch** *(Maintainers)* — When the `develop` branch is ready for release, create a branch named `release/x.y.z` from `develop`. This branch will be tested and validated before merging to `main`.

9. **Open a Pull Request to Upstream `main`** *(Maintainers)* — Once the release is validated, open a PR from the release branch to the upstream `main`. After the merge, maintainers will create a version tag (e.g., `v0.2.0`).

10. **Sync Your Fork** — Finally, update both your fork's `main` and `develop` branches to reflect the latest upstream state.

### Step-by-Step Example

Here is an example of implementing a feature called `custom-evaluator` in the AgentOps repository.

1. **Create a Fork**

   Fork the repository via GitHub UI.

2. **Clone Your Fork Locally**

   ```bash
   git clone https://github.com/<your-github-user>/agentops.git
   ```

3. **Set Upstream Remote**

   ```bash
   git remote add upstream git@github.com:<upstream-org>/agentops.git
   ```

4. **Create a Feature Branch**

   ```bash
   git checkout -b feature/custom-evaluator develop
   ```

5. **Make and Push Your Changes**

   ```bash
   git add .
   git commit -m "Add custom evaluator support"
   git push origin feature/custom-evaluator
   ```

6. **Open and Merge the Pull Request to `develop`**

   - Go to your fork on GitHub and click **New Pull Request**.
   - Base: `upstream/agentops` → `develop`
   - Compare: `<your-github-user>/agentops` → `feature/custom-evaluator`
   - Maintainers will review, request changes if needed, and merge.

7. **Sync Your Fork's `develop`**

   ```bash
   git fetch upstream
   git checkout develop
   git merge upstream/develop
   git push origin develop
   ```

8. **Create a Release Branch** *(Maintainers)*

   ```bash
   git checkout -b release/0.2.0 develop
   git push origin release/0.2.0
   ```

9. **Open a Pull Request to Upstream `main`** *(Maintainers)*

   - Base: `upstream/agentops` → `main`
   - Compare: `release/0.2.0`
   - After review and merge, maintainers tag the release (e.g., `v0.2.0`).

10. **Sync Your Fork After Tag Creation**

    ```bash
    git fetch upstream
    git checkout main
    git merge upstream/main
    git push origin main
    ```

## Legal and Code of Conduct

Before contributing, you'll need to sign a Contributor License Agreement (CLA) to confirm that you have the rights to, and do, grant us permission to use your contribution. More details can be found at [Microsoft CLA](https://cla.opensource.microsoft.com).

This project adheres to the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information, please visit the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any questions or comments.
