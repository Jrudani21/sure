# Code style

- Python 3.9+, standard library only. No new dependency.
- One source file, `sure/sure.py`. Split only when it is too long to hold in your head.
- Plain functions and dicts. No classes, interfaces, or config layers until a second use exists.
- `main` and anything called from a hook must never raise; catch at the boundary.
- Mark deliberate simplifications that have a known ceiling with a `ponytail:` comment naming the ceiling and the upgrade path.
- Card wording lives in the `CARDS` table, not scattered in code.
- Keep regex rules in `BASH_RULES` order: most dangerous first, first match wins.
- Comments say why, not what. Short names, short functions.
- When editing files from scripts on Windows, write with `newline=""` and check the result parses (`python -c "import ast;ast.parse(open('sure/sure.py').read())"`).
