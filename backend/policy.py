"""Pre-flight policy gate: static security scan of AI-written code BEFORE it is allowed to run.

This is defense in depth, not the security boundary. The sandbox is the boundary; this gate stops obviously
out-of-scope code early and turns it into feedback for the agent. The Attack Lab deliberately skips this gate
to prove the sandbox holds on its own.
"""
import ast

ALLOWED_MODULES = {
    "pandas", "numpy", "scipy", "statsmodels", "matplotlib", "seaborn",
    "json", "math", "statistics", "datetime", "re", "collections", "itertools", "functools",
    "warnings", "calendar", "decimal", "fractions", "string", "textwrap", "typing",
}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "input", "globals", "locals", "vars"}
BLOCKED_ATTRS = {"system", "popen", "spawn", "fork", "rmtree", "kill", "connect", "urlopen"}  # not "remove": list.remove is common
ALLOWED_FILES = {"data.csv", "chart.png"}


def check(code: str) -> dict:
    violations: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"verdict": "blocked", "violations": [f"SyntaxError: {exc.msg} (line {exc.lineno})"]}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    violations.append(f"import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                violations.append(f"import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BLOCKED_CALLS:
                violations.append(f"call to '{fn.id}()' is not allowed")
            if isinstance(fn, ast.Attribute) and fn.attr in BLOCKED_ATTRS:
                violations.append(f"call to '.{fn.attr}()' is not allowed")
            if isinstance(fn, ast.Name) and fn.id == "open" and node.args:
                target = node.args[0]
                if not (isinstance(target, ast.Constant) and target.value in ALLOWED_FILES):
                    violations.append("open() may only access data.csv or chart.png")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr != "__name__":
            violations.append(f"dunder access '{node.attr}' is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.lower().startswith(("http://", "https://", "ftp://")):
                violations.append(f"network URL in code: {node.value[:60]}")

    unique = list(dict.fromkeys(violations))
    return {"verdict": "blocked" if unique else "pass", "violations": unique[:8]}
