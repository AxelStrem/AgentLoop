<projects/project_definition.os/definition> definition {
    - description = "Quill sets books in type: a Python typesetter that turns Markdown into print-ready PDF.\n\n## Rules\n\n- Never print to `{press}` from a test: the tests write PDF files to the scratch folder.\n\n## Checking your work\n\nRun `python -m pytest -q` in `{quill}`, and `{python} {bundle}/tools/look.py` on the PDF it made.\n"
    - Repos {
        - {
            - name = "quill"
            - about = "The typesetter: the code, its tests and its docs."
        }
    }
    - Places {
        - {
            - name = "press"
            - about = "The printer driver, installed. Read only."
        }
    }
    - Tools {
        - {
            - file = "look.py"
            - sha256 = "3a66aebdedbad3cf107d24e72a07d4b735819b1cf4020fdd922f63c064708172"
            - systems = ""
        }
    }
    - systems = "windows"
    - settings = "{\"maxIterations\": 10}"
}
