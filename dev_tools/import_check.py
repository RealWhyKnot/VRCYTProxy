import sys
import os
import ast
from pathlib import Path

def get_defined_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name): names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name): names.add(elt.id)
    return names

def check_file_static_symbols(file_path, project_root):
    with open(file_path, "r", encoding="utf-8") as f:
        try: tree = ast.parse(f.read())
        except Exception as e: return [f"AST Parse Error: {e}"]

    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module: continue
            module_path = None
            if node.level > 0:
                path = Path(file_path).parent
                for _ in range(node.level - 1): path = path.parent
                if node.module: path = path.joinpath(*node.module.split("."))
                module_path = path
            else:
                path = project_root.joinpath(*node.module.split("."))
                if path.with_suffix(".py").exists() or (path.is_dir() and path.joinpath("__init__.py").exists()):
                    module_path = path
            if not module_path: continue
            target_file = None
            if module_path.with_suffix(".py").exists(): target_file = module_path.with_suffix(".py")
            elif module_path.is_dir() and module_path.joinpath("__init__.py").exists(): target_file = module_path.joinpath("__init__.py")
            if target_file:
                with open(target_file, "r", encoding="utf-8") as f_target:
                    try:
                        target_tree = ast.parse(f_target.read())
                        existing_names = get_defined_names(target_tree)
                        for alias in node.names:
                            if alias.name != "*" and alias.name not in existing_names:
                                errors.append(f"Symbol '{alias.name}' not found in '{node.module}'")
                    except: pass
    return errors

if __name__ == "__main__":
    src_dir = Path(sys.argv[1])
    success = True
    for path in src_dir.rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "build", "dist"]): continue
        errs = check_file_static_symbols(path, src_dir)
        if errs:
            print(f"\n[FAIL] {path}")
            for e in errs: print(f"  - {e}")
            success = False
    if not success: sys.exit(1)
    sys.exit(0)
