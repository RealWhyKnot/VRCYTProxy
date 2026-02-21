import ast
import sys
import os
from pathlib import Path

class NameChecker(ast.NodeVisitor):
    def __init__(self, filename, content):
        self.filename = filename
        self.content_lines = content.splitlines()
        self.errors = []
        self.scopes = [set()]
        import builtins
        self.scopes[0].update(dir(builtins))
        self.scopes[0].update(['__file__', '__name__', 'True', 'False', 'None', 'classmethod', 'staticmethod', 'property', 'id', 'next', 'iter', 'len', 'range', 'enumerate', 'any', 'all', 'sum', 'min', 'max', 'sorted', 'round', 'float', 'int', 'str', 'dict', 'list', 'set', 'bool', 'Exception', 'ValueError', 'TypeError', 'StopIteration', 'ImportError', 'FileNotFoundError'])

    def define_globals(self, tree):
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.define(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._handle_assignment_target(target)
            elif isinstance(node, ast.AnnAssign):
                self._handle_assignment_target(node.target)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    self.define(alias.asname or alias.name.split('.')[0])

    def error(self, node, msg):
        line = node.lineno
        col = node.col_offset
        text = self.content_lines[line-1] if line <= len(self.content_lines) else ""
        self.errors.append(f"{self.filename}:{line}:{col}: {msg}\n  -> {text.strip()}")

    def define(self, name):
        self.scopes[-1].add(name)

    def is_defined(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return True
        return False

    def visit_Import(self, node):
        for alias in node.names:
            self.define(alias.asname or alias.name.split('.')[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.define(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.define(node.name)
        self.scopes.append(set())
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node):
        self.define(node.name)
        new_scope = set()
        for arg in node.args.args:
            new_scope.add(arg.arg)
        if node.args.vararg: new_scope.add(node.args.vararg.arg)
        if node.args.kwarg: new_scope.add(node.args.kwarg.arg)
        self.scopes.append(new_scope)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node):
        self.scopes.append(set())
        for arg in node.args.args:
            self.define(arg.arg)
        if node.args.vararg: self.define(node.args.vararg.arg)
        if node.args.kwarg: self.define(node.args.kwarg.arg)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ListComp(self, node):
        self._visit_comp(node)

    def visit_SetComp(self, node):
        self._visit_comp(node)

    def visit_DictComp(self, node):
        self._visit_comp(node)

    def visit_GeneratorExp(self, node):
        self._visit_comp(node)

    def _visit_comp(self, node):
        self.scopes.append(set())
        for gen in node.generators:
            self._handle_assignment_target(gen.target)
            self.visit(gen.iter)
            for if_clause in gen.ifs:
                self.visit(if_clause)
        if hasattr(node, 'elt'): self.visit(node.elt)
        if hasattr(node, 'key'): self.visit(node.key)
        if hasattr(node, 'value'): self.visit(node.value)
        self.scopes.pop()

    def visit_Assign(self, node):
        for target in node.targets:
            self._handle_assignment_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._handle_assignment_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        self._handle_assignment_target(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node):
        self.visit_For(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                self._handle_assignment_target(item.optional_vars)
        self.generic_visit(node)

    def visit_AsyncWith(self, node):
        self.visit_With(node)

    def _handle_assignment_target(self, target):
        if isinstance(target, ast.Name): self.define(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts: self._handle_assignment_target(elt)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            if not self.is_defined(node.id):
                self.error(node, f"Undefined name '{node.id}'")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name: self.define(node.name)
        self.generic_visit(node)

def check_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f: content = f.read()
        tree = ast.parse(content)
        checker = NameChecker(path.name, content)
        checker.define_globals(tree)
        checker.visit(tree)
        return checker.errors
    except Exception as e: return [f"ERROR PARSING {path.name}: {e}"]

def run_name_checks(directory):
    print(f"Deep checking names and variables in {directory}...")
    root = Path(directory)
    all_errors = {}
    for path in root.rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "node_modules", "__pycache__", "build", "dist"]): continue
        errors = check_file(path)
        if errors: all_errors[str(path)] = errors
    if all_errors:
        print("\n[CRITICAL] Undefined names found!")
        for path, errors in all_errors.items():
            print(f"\nFile: {path}"); [print(f"  - {err}") for err in errors]
        return False
    print("[SUCCESS] No undefined names found.")
    return True

if __name__ == "__main__":
    import os
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.getcwd())
    if not run_name_checks(src_dir): sys.exit(1)
    sys.exit(0)
