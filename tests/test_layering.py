"""Structural guards for the layering the refactor established.

These assert properties that are easy to erode silently: the SQL boundary, the
import direction, and the leaf-ness of the shared constant modules. They fail
loudly if a future change reaches through a layer instead of extending it.
"""

import ast
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "cdash_digester"


def _py_files(*rel_dirs):
    for rel in rel_dirs:
        base = SRC / rel if rel else SRC
        for p in base.rglob("*.py") if rel else base.glob("*.py"):
            yield p


def test_no_sql_execution_outside_the_db_package():
    """Raw SQL belongs in db/. Services used to run four export joins and two
    cascade deletes through BatchDB._con, which put the densest schema
    knowledge in the app outside the layer built to hold it.

    Detects execute()/executemany() calls via the AST rather than grepping for
    SQL keywords, which would also hit the word "SELECT" in prose.
    """
    offenders = []
    for path in list(_py_files("services")) + list(_py_files("gui")) + list(_py_files("")):
        if path.parent.name == "db":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("execute", "executemany")):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, "SQL executed outside db/: " + "; ".join(offenders)


def test_gui_does_not_import_persistence_or_services():
    """The GUI talks to Digester only — never to db/ or a service.

    `models` is deliberately allowed: the panes read entities by attribute
    rather than by column-name string, so they depend on the domain *type*.
    That is ordinary layering (models is a leaf), and strictly better than the
    previous state, where the GUI was coupled to column names with no type at
    all and a rename broke it with a KeyError at paint time.
    """
    forbidden = ("cdash_digester.db", "cdash_digester.services",
                 "..db", "..services")
    offenders = []
    for path in (SRC / "gui").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = ("." * node.level) + node.module
                if any(mod.startswith(f) for f in forbidden):
                    offenders.append(f"{path.name} imports {mod}")
    assert not offenders, offenders


def test_constants_and_naming_are_leaf_modules():
    """Both must import nothing from this package, so every layer can depend
    on them. They lived in cdash_objects.py, which forced db/repositories.py to
    import upwards into the persistence facade and created a real cycle."""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import cdash_digester.constants, cdash_digester.naming;"
        "print([m for m in sys.modules if m.startswith('cdash_digester.') "
        "and m not in ('cdash_digester.constants','cdash_digester.naming')])"
        % str(SRC.parent)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", f"not leaves: {out.stdout}"


def test_repositories_does_not_import_cdash_objects():
    """The former cycle: repositories needed constants that lived beside
    BatchDB, and BatchDB needed repositories."""
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import cdash_digester.db.repositories;"
        "print('cdash_digester.cdash_objects' in sys.modules)"
        % str(SRC.parent)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_services_do_not_import_the_digester_facade():
    """Services depend on Session, never on the facade above them."""
    offenders = []
    for path in (SRC / "services").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "digester" in node.module:
                    offenders.append(f"{path.name} imports {node.module}")
    assert not offenders, offenders


def test_services_hold_no_dig_back_reference():
    """Every service used to keep `self._dig` — the Digester facade — and reach
    through it into private attributes (dig._collect_and_store_counts,
    dig._validation, dig._validator). Siblings are explicit constructor
    arguments now."""
    offenders = []
    for path in (SRC / "services").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_dig" in text:
            offenders.append(path.name)
    assert not offenders, f"services still hold a Digester back-reference: {offenders}"


def test_services_touch_no_private_session_members():
    """A service reaching for session._foo would be the old smell returning."""
    offenders = []
    for path in (SRC / "services").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("session._", "self._session._"):
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, offenders
