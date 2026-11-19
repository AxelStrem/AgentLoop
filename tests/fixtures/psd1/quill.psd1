# What run-agent-loop.ps1 needs to work this project's tracker: .\run-agent-loop.ps1 quill
# The brief the agent reads beside AUTOTASK.md is quill.md, next to this file. Paths are written
# out in full: a data file like this one cannot hold variables such as $HOME.
@{
    # The tracker document the items are in. It has to be in a git repository: the loop commits
    # each session's edits to it.
    # Tracker = "D:\Work\Notes\shared\documents\someone\projects\quill.os"
    Tracker = "someone/projects/quill.os"
    Server = "https://tracker.example.com"
    KeyFile = "C:\Users\someone\.overseer\agent_someone.key"


    # Or the tracker on the Overseer server, reached as the agents' account: Tracker is then its
    # name there, and nothing of it is committed here - every edit is sent as it is made.
    # Tracker = "someone/projects/quill.os"
    # Server = "https://<your domain>"
    # KeyFile = "C:\Users\someone\.overseer\agent_someone.key"

    # The repositories the agent may change; each session's changes are committed in each of them.
    # Sessions run in the first. The tracker's own repository is added to these.
    Repos = @("D:\Work\Quill")

    # Which Claude account runs the sessions: N for the config directory ~\.claude-accountN,
    # logged in to once by hand. -Account overrides it for one run.
    Account = 1

    # Models for this project, where they differ from the script's: any of the script's $Routes
    # keys (triage, trivial, low, medium, high, unassigned), Escalation, and BuildAfterTriage.
    # Routes = @{ low = @{ model = "opus"; effort = "medium" } }
}
