# agentops-toolkit (deprecated)

> ⚠️ **This package has been renamed.** `agentops-toolkit` is no longer
> maintained. The project now ships as
> [`agentops-accelerator`](https://pypi.org/project/agentops-accelerator/).
> This release (`0.3.0`) exists only as a redirect — installing it will pull in
> `agentops-accelerator` automatically.

## Migration

Uninstall the old distribution and install the renamed one:

```bash
pip uninstall agentops-toolkit
pip install agentops-accelerator
```

Or, in a single step:

```bash
pip install --upgrade agentops-accelerator
```

## What stays the same

The **import path is unchanged**. Existing code does not need to be updated:

```python
import agentops
```

All modules, CLI entrypoints (`agentops ...`), and the public API are
preserved exactly. Only the PyPI distribution name has changed.

## Version note

The final shipped version of the original `agentops-toolkit` distribution was
in the `0.2.x` series. This `0.3.0` placeholder intentionally aligns with the
first release of `agentops-accelerator` (`>=0.3.0`) so that
`pip install agentops-toolkit` resolves to the new package without surprises.

## Background

The rename is tracked in
[Azure/agentops#181](https://github.com/Azure/agentops/issues/181). See the
[repository](https://github.com/Azure/agentops) for the active project,
changelog, and documentation.
