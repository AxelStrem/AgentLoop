## On Windows

The Bash tool is Git Bash, and it is the one to use for git, python, cargo and npm. The PowerShell
tool is Windows PowerShell, which in 5.1 has no `&&` and turns what a program writes to stderr into
errors: use it only for what needs it.

- **Git Bash's own paths** - `/tmp/...`, `/e/Source/...` - mean nothing to the Windows programs
  it starts: python and node read `/tmp/x` as `\tmp\x` on the current drive, and fail. Give
  them Windows paths with forward slashes, `C:/src/...`, which every program and both
  shells read.
