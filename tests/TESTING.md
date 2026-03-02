# Running Tests

This guide explains how to run tests for this project in a generic way, independent of your local machine path.

## Prerequisites

- Python 3.11+
- A virtual environment (`.venv`) in the repository root

## 1) Open a terminal at the repository root

All commands below assume your terminal is opened in the project root.

## 2) Activate the virtual environment

### PowerShell (Windows)

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once in the current shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 3) Install project and test dependencies

```powershell
python -m pip install -e .
python -m pip install pytest
```

## 4) Run all unit tests

```powershell
python -m pytest tests/unit -q
```

## Useful commands

Run one specific test file:

```powershell
python -m pytest tests/unit/test_models.py -q
```

Verbose output:

```powershell
python -m pytest tests/unit -vv
```

Stop on first failure:

```powershell
python -m pytest tests/unit -x
```

Run integration tests:

```powershell
python -m pytest tests/integration -q
```

## Troubleshooting

Check which Python is active:

```powershell
where python
python --version
```

Check pytest in current environment:

```powershell
python -m pytest --version
```
