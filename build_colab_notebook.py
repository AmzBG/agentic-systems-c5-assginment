"""Build a Colab launcher that loads the source from the submission archive."""
import ast
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent

def main():
    setup = '''from pathlib import Path
import os, subprocess, sys, zipfile
from uuid import uuid4

archive_path = Path('/content/C5_Cedar_Crumb_Submission.zip')
if not archive_path.is_file():
    from google.colab import files
    print('Select C5_Cedar_Crumb_Submission.zip')
    os.chdir('/content')
    files.upload()
if not archive_path.is_file():
    raise FileNotFoundError('Upload C5_Cedar_Crumb_Submission.zip into /content.')
run_root = Path('/content') / f'cedar-run-{uuid4().hex[:8]}'
run_root.mkdir()
with zipfile.ZipFile(archive_path) as archive:
    for entry in archive.infolist():
        if not (run_root / entry.filename).resolve().is_relative_to(run_root.resolve()):
            raise ValueError('Unsafe archive path.')
    archive.extractall(run_root)
PROJECT = run_root / 'cedar_crumb_c5'
assert (PROJECT / 'app.py').is_file(), 'Upload the current submission ZIP containing app.py.'

def command(args):
    with subprocess.Popen(args, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as process:
        for line in process.stdout:
            print(line, end='', flush=True)
        status = process.wait()
    if status:
        raise RuntimeError(f'Command failed (exit {status}); see output above.')

command([sys.executable, '-m', 'pip', 'install', '--quiet', 'uv==0.13.6'])
UV = [sys.executable, '-m', 'uv']
ENV = Path('/content/cedar_crumb_py312')
command(UV + ['venv', '--python', '3.12', '--managed-python', '--allow-existing', str(ENV)])
PYTHON = str(ENV / 'bin/python')
command(UV + ['pip', 'install', '--python', PYTHON, '-r', 'requirements-dev.txt'])
command(UV + ['pip', 'check', '--python', PYTHON])
if not os.environ.get('GEMINI_API_KEY'):
    from google.colab import userdata
    os.environ['GEMINI_API_KEY'] = userdata.get('GEMINI_API_KEY')
assert os.environ.get('GEMINI_API_KEY'), 'Enable notebook access for GEMINI_API_KEY in Colab Secrets.'
os.environ.setdefault('GEMINI_MODEL', 'gemini-3.5-flash-lite')
print('Cedar Crumb installed. Key loaded privately.')
'''
    tests = "command([PYTHON, '-m', 'pytest', '-q'])\ncommand([PYTHON, 'smoke_test.py'])\n"
    launch = '''import re, time
from IPython.display import HTML, display
if 'APP_PROCESS' in globals() and APP_PROCESS.poll() is None:
    APP_PROCESS.terminate()
    APP_PROCESS.wait(timeout=20)
LOG_PATH = PROJECT / 'app.log'
with LOG_PATH.open('w') as log:
    APP_PROCESS = subprocess.Popen([PYTHON, '-u', 'app.py', '--share'], cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT)
url = None
for _ in range(180):
    text = LOG_PATH.read_text(errors='replace')
    match = re.search(r'https://[a-zA-Z0-9-]+\\.gradio\\.live', text)
    if match:
        url = match.group(0)
        break
    if APP_PROCESS.poll() is not None:
        raise RuntimeError('App startup failed: ' + text)
    time.sleep(1)
if not url:
    raise RuntimeError('No share link received: ' + LOG_PATH.read_text())
print('Open Cedar Crumb:', url)
display(HTML(f'<p><a href="{url}" target="_blank">Open Cedar Crumb in a new tab</a></p><iframe src="{url}" width="100%" height="1000" style="border:0"></iframe>'))
'''
    cells = []
    written = {}

    def cell(kind, source):
        item = {'cell_type': kind, 'id': f'cedar-{len(cells)}', 'metadata': {}, 'source': source.splitlines(keepends=True)}
        if kind == 'code':
            if not source.startswith('%%writefile'):
                ast.parse(source)
            item.update(execution_count=None, outputs=[])
        cells.append(item)

    def module_cell(filename, content=None, append=False):
        content = (ROOT / filename).read_text(encoding='utf-8') if content is None else content
        flag = '-a ' if append else ''
        cell('code', f'%%writefile {flag}"{{PROJECT}}/{filename}"\n' + content)
        written[filename] = written.get(filename, '') + content if append else content

    cell('markdown', '# Cedar Crumb\n**Good bread. Time well spent.**\n\nUpload **C5_Cedar_Crumb_Submission.zip** to **/content**, enable **GEMINI_API_KEY** in Colab Secrets, then select **Runtime → Run all**.\n\nRuntime: CPU · Python 3.12\n\nChat requests use Gemini API quota. The temporary application link is accessible to anyone with the link while the runtime is running.\n')
    cell('markdown', '## 1. Environment\n')
    cell('code', setup)
    cell('markdown', '## 2. Input and output contracts · models.py\n')
    module_cell('models.py')
    cell('markdown', '## 3. Menu and inquiry operations · bakery.py\n')
    module_cell('bakery.py')

    source = (ROOT / 'server.py').read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    factory = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_server')
    tools = [n for n in factory.body if isinstance(n, ast.FunctionDef) and n.decorator_list]
    starts = [min(n.lineno, *(d.lineno for d in n.decorator_list)) - 1 for n in tools]
    cell('markdown', '## 4. MCP Server · server.py\n### Configuration and error handling\n')
    module_cell('server.py', ''.join(lines[:starts[0]]))
    headings = ['Data · search_menu', 'Action · save_pickup_inquiry', 'Orchestration · prepare_pickup_inquiry']
    for index, heading in enumerate(headings):
        end = starts[index + 1] if index + 1 < len(starts) else tools[index].end_lineno
        cell('markdown', '### ' + heading + '\n')
        module_cell('server.py', ''.join(lines[starts[index]:end]), append=True)
    cell('markdown', '### Server startup · stdio\n')
    module_cell('server.py', ''.join(lines[tools[-1].end_lineno:]), append=True)

    source = (ROOT / 'client.py').read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    agent_start = next(n.lineno - 1 for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_agent')
    runner_start = next(n.lineno - 1 for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'decode_output')
    cell('markdown', '## 5. Host and MCP Client · client.py\n\n```text\nCustomer → Host (Agent + Runner) → MCP Client → MCP Server\nCustomer ← Host (final answer)  ← MCP Client ← MCP Server\n```\n\n### MCP Client · subprocess connection\n')
    module_cell('client.py', ''.join(lines[:agent_start]))
    cell('markdown', '### Host · agent and model configuration\n')
    module_cell('client.py', ''.join(lines[agent_start:runner_start]), append=True)
    cell('markdown', '### Agent loop and tool results\n')
    module_cell('client.py', ''.join(lines[runner_start:]), append=True)
    cell('markdown', '## 6. Session connection and schema cache · session_runtime.py\n')
    module_cell('session_runtime.py')
    cell('markdown', '### Chat and request activity · app.py\n')
    module_cell('app.py')
    cell('markdown', '## 7. Tool discovery\n')
    cell('code', '''discovery_script = """import asyncio
import json
from client import connect_server, tool_definition

async def discover():
    async with connect_server('results/discovery.sqlite3') as server:
        tools = await server.list_tools()
        assert len(tools) == 3
        print(json.dumps([tool_definition(t) for t in tools], indent=2))
        print('tools/list: exactly three registered tools; stdio connection completed.')

asyncio.run(discover())
"""
command([PYTHON, '-c', discovery_script])
''')
    cell('markdown', '## 8. Tests\n')
    cell('code', tests)
    cell('markdown', '## 9. Interactive application\n')
    cell('code', launch)
    for name, content in written.items():
        assert content == (ROOT / name).read_text(encoding='utf-8'), name
        ast.parse(content)
    doc = {'cells': cells, 'metadata': {'colab': {'name': 'C5_Cedar_Crumb_Colab.ipynb'}, 'kernelspec': {'display_name': 'Python 3', 'name': 'python3'}, 'language_info': {'name': 'python'}}, 'nbformat': 4, 'nbformat_minor': 5}
    (ROOT / 'C5_Cedar_Crumb_Colab.ipynb').write_text(json.dumps(doc, indent=2) + '\n', encoding='utf-8')
    print('Built notebook; all six application modules match the source and compile.')

if __name__ == '__main__':
    main()
