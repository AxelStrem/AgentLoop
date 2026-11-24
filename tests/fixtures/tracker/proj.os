// A tracker of the tests': one item free, one a loop is working, one for another account, one held.
<projects/project_template.os/project> project (label="Proj") {
    - name = "Proj"
    - description = "A project for tests."
    - Items {
        - {
            - added = "2026-10-01T00:00:00+00:00"
            - handle = "alpha"
            - title = "The alpha item"
            - points = 3
        }
        - {
            - added = "2026-10-01T00:00:00+00:00"
            - handle = "beta"
            - title = "The beta item"
            - stage = "ready"
            - complexity = "low"
            - working = true
            - worker = "sonnet (high)"
        }
        - {
            - added = "2026-10-01T00:00:00+00:00"
            - handle = "gamma"
            - assignee = "someone"
            - title = "The gamma item"
        }
        - {
            - added = "2026-10-01T00:00:00+00:00"
            - handle = "delta"
            - title = "The delta item"
            - stage = "planning"
            - flags = "asked"
            - after = "alpha"
        }
    }
    - History {
    }
}
