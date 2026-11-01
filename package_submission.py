"""Build the source submission archive from an explicit list of project files."""
from pathlib import Path
import ast
import zipfile

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / "C5_Cedar_Crumb_Submission.zip"


def main() -> None:
    summary = (ROOT / "docs" / "summary.md").read_text(encoding="utf-8").split("## References")[0].split("\n", 2)[2]
    words = len(summary.split())
    if not 300 <= words <= 400:
        raise ValueError(f"Summary must contain 300-400 words; found {words}.")
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    tools = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and any(
        isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool"
        for decorator in node.decorator_list)]
    if set(tools) != {"search_menu", "save_pickup_inquiry", "prepare_pickup_inquiry"} or len(tools) != 3:
        raise ValueError("The server must expose exactly the three required tools.")
    files = [ROOT / name for name in [
        "README.md", "bakery.py", "models.py", "server.py", "client.py", "session_runtime.py", "app.py", "smoke_test.py",
        "package_submission.py", "requirements.txt", "requirements-dev.txt", "requirements-lock.txt",
        "pytest.ini", ".gitignore", ".env.example", "data/bakery.json",
        "C5_Cedar_Crumb_Colab.ipynb", "build_colab_notebook.py",
    ]]
    files += [ROOT / "docs" / name for name in ("summary.md", "reflection.md", "verification.md")]
    files += list((ROOT / "tests").glob("*.py"))
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path("cedar_crumb_c5") / path.relative_to(ROOT))
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise ValueError("Submission archive validation failed.")
        if any(Path(name).name == ".env" or ".git" in Path(name).parts or name.endswith(".sqlite3") for name in archive.namelist()):
            raise ValueError("Private or generated files must not enter the submission archive.")
    print(f"Created {OUTPUT.name}: {len(files)} files; summary {words} words; exactly three MCP tools.")


if __name__ == "__main__":
    main()
