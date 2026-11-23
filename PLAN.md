# CIParity 0.1 implementation plan

The starting point on main (7926f60) contains the repository title and Aleksandar Nikolić's MIT licence. The distribution and executable use the requested name CIParity. Preserve the existing licence.

1. Package a Python 3.11+ src-layout project with Typer, typed data records, a stable JSON envelope, pytest, Ruff and mypy.
2. Collect local observations separately from user-supplied test context. Restrict repository reads, environment output and optional host tool probes.
3. Parse workflow declarations with a bounded YAML reader. Expand static matrices, apply include/exclude and resolve direct matrix references. Keep conditions, action boundaries and unknowns visible.
4. Compare each property at its actual scope. Attach local and declared evidence, a status, severity, impact and a practical next check. Keep runtime observations distinct.
5. Expose inspection, comparison, reports and doctor via one CLI. Require unambiguous workflow, job and variant selection. Provide an example repository for predictable comparisons.
6. Exercise units, integration and security boundaries, configure Linux/macOS/Windows CI, build and install the package, and document supported semantics and limitations.
