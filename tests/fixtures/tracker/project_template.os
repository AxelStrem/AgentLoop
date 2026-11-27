// The project template, cut down to what tracker.py reads of it: its version, stages and complexities,
// and the Discussion and Features lists, declared and empty, that a tracker writes no line of until it has one.
tab project (label=$(name), mutable=true) {
    int template_version (hidden=true) = 2
    list Stages (entry=<Label>, key="tag") {
        - {
            - tag = "filed"
        }
        - {
            - tag = "planning"
        }
        - {
            - tag = "ready"
        }
        - {
            - tag = "testing"
        }
        - {
            - tag = "parts"
        }
        - {
            - tag = "later"
        }
        - {
            - tag = "finished"
        }
        - {
            - tag = "cancelled"
        }
    }
    list Complexities (entry=<Label>, key="tag") {
        - {
            - tag = "unassigned"
        }
        - {
            - tag = "trivial"
        }
        - {
            - tag = "low"
        }
        - {
            - tag = "medium"
        }
        - {
            - tag = "high"
        }
    }
    list Discussion (entry=<Topic>, key="handle") { }
    list Features (entry=<Feature>, key="handle", view="canvas") { }
}
