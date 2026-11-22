# Security

Report security issues privately through GitHub's private vulnerability reporting for this repository if enabled, or contact the maintainer privately via their GitHub profile. A useful report includes the installed version, OS, command and a minimal synthetic workflow. Remove private values before sharing a sample. [Maintainer profile](https://github.com/AleksZyro)

## Trust boundary

The scanner treats workflow and project metadata as input data. It reads bounded, regular files beneath one resolved project root. Static inspection assumes the checkout stays in place during a scan. For a directory being modified concurrently by another user or process, inspect a stable snapshot.

Install CIParity in a trusted Python environment. Run the installed console script from that environment and use `--root` to select an unfamiliar checkout. A Python startup path, system executable or library controlled by another party is outside this boundary.

The default inspector starts zero tool probes. The optional `inspect local --tool-versions` trusts native executables on the host's absolute PATH entries outside the project. Each recognised probe uses fixed arguments, an isolated working directory, a minimal environment, disabled stdin and a two-second timeout.

CIParity exposes structural identifiers, including workflow filenames, job and step IDs and declared environment variable names. Local inspection includes the current directory and interpreter path. Treat these identifiers as public metadata when sharing a report. Arbitrary environment values, workflow scripts, action inputs, display names, dependency URLs and unrecognised matrix values are withheld. Error messages provide safe context and omit source snippets. The scanner uses no network services or telemetry.
