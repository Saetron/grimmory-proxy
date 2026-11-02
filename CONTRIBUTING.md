# Contributing to grimmory-proxy

Thank you for your interest in contributing to **grimmory-proxy**! We welcome bug fixes, compatibility improvements with Komga reader applications, performance optimizations, and documentation enhancements.

---

## Code of Conduct

All contributors and participants are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Development Workflow

### 1. Branching Strategy
- **`main`**: Production release branch. Reflects the latest stable release tagged and published as a Docker container.
- **`dev`**: Active development branch. All pull requests, feature branches, and bug fixes should branch from and target **`dev`**.

### 2. Local Environment Setup
1. Fork the repository and clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/grimmory-proxy.git
   cd grimmory-proxy
   git checkout dev
   ```

2. Create and activate a Python virtual environment (Python 3.11+ recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Run the local server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```

---

## AI-Assisted Development

Contributions developed using AI tools, LLM coding assistants (such as Antigravity, GitHub Copilot, Claude, Cursor, ChatGPT, etc.), or agentic workflows are **fully allowed and welcome**.

When contributing AI-assisted code, please keep the following in mind:
- **Human Verification**: You are responsible for reviewing, understanding, and verifying all generated code before submitting.
- **Test Verification**: All changes must pass the full test suite (pytest) without errors or regressions.
- **Architecture & Specifications**: Ensure that changes adhere to the upstream [Komga OpenAPI specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json) and [Grimmory API](https://grimmory.org/api/).
- **Changelog & Documentation**: Remember to update CHANGELOG.md under ## [Unreleased].

---

## Testing & Verification

Before submitting any code changes, ensure the entire test suite passes cleanly:

```bash
pytest
```

If adding new features or fixing bugs:
- Add corresponding unit and integration tests under `tests/`.
- Ensure all 100+ existing tests pass without regressions or deprecation warnings.
- Verify upstream invariants with the [Komga OpenAPI specification](https://raw.githubusercontent.com/gotson/komga/refs/heads/master/komga/docs/openapi.json) and [Grimmory API](https://grimmory.org/api/).

---

## Changelog Maintenance

Whenever you implement a feature, fix a bug, optimize performance, or modify dependencies:
- Update **[CHANGELOG.md](CHANGELOG.md)** under the `## [Unreleased]` section.
- Categorize your changes using standard Keep a Changelog headers (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`, `Performance`, `Dependencies`).

---

## Commit Guidelines

We use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat(scope): add feature description`
- `fix(scope): resolve bug description`
- `refactor(scope): internal refactoring without behavior change`
- `perf(scope): performance improvements`
- `test(scope): add or update tests`
- `docs(scope): documentation updates`
- `chore(deps): dependency or tooling updates`

---

## Submitting a Pull Request

1. Push your changes to your fork:
   ```bash
   git push origin my-feature-branch
   ```
2. Open a Pull Request targeting the **`dev`** branch.
3. Complete the [Pull Request Template](.github/pull_request_template.md).
4. Automated GitHub Actions CI will run the test suite and CodeQL analysis on your PR.
