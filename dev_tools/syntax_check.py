import sys
import py_compile
import ast
from pathlib import Path

def check_syntax(directory):
    print(f"Deep checking syntax in {directory}...")
    success = True
    for path in Path(directory).rglob("*.py"):
        if any(x in str(path) for x in [".old", ".venv", "node_modules", "__pycache__", "build", "dist"]):
            continue
            
        # 1. Bytecode compilation check
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"\n[FAIL] Bytecode Compilation Error in {path}:")
            print(e)
            success = False
            continue
            
        # 2. AST Parsing check
        try:
            with open(path, "r", encoding="utf-8") as f:
                ast.parse(f.read())
        except SyntaxError as e:
            print(f"\n[FAIL] AST Syntax Error in {path}:")
            print(f"  Line {e.lineno}: {e.msg}")
            if e.text:
                print(f"  Code: {e.text.strip()}")
            success = False
        except Exception as e:
            print(f"\n[FAIL] Unexpected parsing error in {path}: {e}")
            success = False
            
    return success

if __name__ == "__main__":
    import os
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.getcwd())
    if not check_syntax(src_dir):
        sys.exit(1)
    sys.exit(0)

