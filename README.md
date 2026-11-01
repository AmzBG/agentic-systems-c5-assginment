# Cedar Crumb

**Good bread. Time well spent.**

Chapter 5 - Agent Tools

Cedar Crumb's pickup assistant connects menu information to a dependable inquiry workflow. Customers can review loaves, ingredients, weights, and prices; save a request for a specific product; or ask the assistant to find a loaf and prepare its pickup inquiry.

Nour helps customers explore Cedar Crumb's six-product menu and prepare pickup inquiries for the Hamra bakery.

## Three tools

| Category | Tool | Responsibility | Structured output | Example failure |
| --- | --- | --- | --- | --- |
| Data | `search_menu` | Retrieve menu and pickup information | `MenuResult`, containing `Product` records | `INVALID_QUERY` |
| Action | `save_pickup_inquiry` | Persist one authorized pickup inquiry | `PickupInquiry` | `PRODUCT_NOT_FOUND`, `CONSENT_REQUIRED`, `PICKUP_TOO_SOON`, `PICKUP_CLOSED`, `REQUEST_CONFLICT`, `STORAGE_UNAVAILABLE` |
| Orchestration | `prepare_pickup_inquiry` | Coordinate menu lookup and inquiry saving | `PreparedInquiry`, containing steps and the saved inquiry | `NO_MATCH`, `AMBIGUOUS_MATCH`, propagated Action errors |

Exactly these three capabilities are registered with `@mcp.tool`. The parameterized menu lookup is exposed as a Data tool. No resources or extra callable tools are registered. Local inspection and date helpers are ordinary Python functions.

## Menu

| Product ID | Bread | Weight | USD per loaf |
| --- | --- | ---: | ---: |
| `plain` | Country Sourdough | 800 g | 6.00 |
| `whole-wheat` | Whole Wheat Sourdough | 750 g | 6.00 |
| `rye` | Seeded Rye | 700 g | 7.00 |
| `olive-rosemary` | Olive & Rosemary Sourdough | 800 g | 8.00 |
| `zaatar-sesame` | Zaatar & Sesame Sourdough | 800 g | 7.00 |
| `walnut-cranberry` | Walnut & Cranberry Sourdough | 800 g | 9.00 |

Menu facts and policies are in `data/bakery.json`. All loaves contain gluten; the returned menu includes product allergens and the shared-kitchen notice.

## Architecture

```text
Customer request
       |
       v
Host: OpenAI Agents SDK Agent + Runner in client.py
       | Gemini selects a discovered tool and its arguments
       v
Client: MCPServerStdio
       | tools/list and tools/call over stdio
       v
Server: FastMCP in server.py
       |
       +--> Data: search_menu ------------------> menu JSON
       +--> Action: save_pickup_inquiry ---------> SQLite
       +--> Orchestration: prepare_pickup_inquiry
                   | calls the same Data and Action operations
                   v
            typed result -> Client -> Host -> customer answer
```

The Orchestration tool calls `Bakery.search_menu` followed by `Bakery.save_pickup_inquiry`. The standalone Data and Action tools use those same methods. Internal coordination does not require a second MCP connection or a duplicate implementation.

## Setup

Open **C5_Cedar_Crumb_Colab.ipynb** in Colab, upload **C5_Cedar_Crumb_Submission.zip** to **/content**, add **GEMINI_API_KEY** in Secrets with notebook access enabled, and choose **Runtime → Run all**. No Drive mount, GPU, or code changes are needed. The notebook extracts the separate source files, installs managed Python 3.12, verifies the toolkit, and opens the interactive chat. Use the embedded interface or its temporary Gradio link. Requests use Gemini API quota.

The interface includes conversation history, the established menu, actual MCP calls/results, saved inquiry inspection, and five example prompts for the recording. Each browser session uses its own database. New conversation starts a separate session; previous records remain in the runtime. The temporary share link is accessible to anyone with the link while the app runs; close the runtime when finished.

For local browser chat after installation, run `python app.py`. The CLI and checked walkthrough remain available below.

Use Python **3.12**. From this folder on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

On macOS/Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
test -f .env || cp .env.example .env
```

Set the following privately in `.env`:

```dotenv
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.5-flash-lite
```

The application uses FastMCP **4.1.4** and OpenAI Agents SDK **0.24.0**. `tzdata` supplies the Beirut timezone database on Windows. The complete tested dependency set is recorded in `requirements-lock.txt`; use that file instead of `requirements-dev.txt` when reproducing the full environment.

Gemini is accessed through Google's OpenAI-compatible Chat Completions endpoint. The OpenAI Agents SDK remains the Host/runtime, and `MCPServerStdio` remains the MCP client. A separate server startup command is unnecessary: the client starts and closes the FastMCP process automatically.

## Run

```powershell
.\.venv\Scripts\python.exe client.py
```

For one request:

```powershell
.\.venv\Scripts\python.exe client.py --prompt "Which loaves contain sesame?"
```

For the checked live walkthrough, use a fresh database filename:

```powershell
.\.venv\Scripts\python.exe client.py --demo --database data/walkthrough-take1.sqlite3
.\.venv\Scripts\python.exe bakery.py data/walkthrough-take1.sqlite3
```

The walkthrough uses five real model interactions: Data lookup, Action save, Orchestration, a no-match failure, and an identical retry. Each step checks the chosen tool, saved-row count, and relevant result. The successful requests estimate two Country Sourdough loaves at **USD 12.00** and three Olive & Rosemary loaves at **USD 24.00**. Only two inquiries should exist after the sequence. A retry must retain the first inquiry's reference.

`--demo` computes a suitable future pickup time so the example does not expire. Reusing a populated walkthrough database is rejected; choose a new filename for another take. Normal interactive requests use `data/pickup.sqlite3`. `--database` overrides that path.

The notebook contains the application code in cells organized by contracts, business operations, Data, Action, Orchestration, Host/Client/Server, and chat. The cells write the runtime modules before tool discovery and testing.

In the browser, **Tool activity** shows a current-request timeline and a detailed trace grouped by request reference: customer request; actual `tools/list` request and returned names, descriptions, and schemas; schemas supplied to the model; selected tool and arguments; actual `tools/call` request and result; delivery to the agent; and the final answer. **Saved inquiries** shows persistence independently of the model response.

### Session connections and schema caching

Each chat has one MCP connection owned by a dedicated asynchronous task. The same task opens the connection, executes requests, and closes it. Later requests in that session reuse the three tool definitions and visibly identify cross-turn cache hits. Reuse within one turn is labeled separately. New conversations, reconnects, failed agent runs, and tool-list change notifications invalidate the relevant cache. Reset closes the previous connection; idle connections close after 15 minutes.

Only tool definitions are cached. Each Data lookup rereads the menu, and each Action or Orchestration call executes against the current data and database. Retrying a saved request still invokes the Action and returns its existing reference. Caching avoids repeated MCP discovery and metadata transfer; the model still receives the function schemas required for every model request. This is separate from any provider-side prompt caching.

The cache uses the SDK's `cache_tools_list` and `invalidate_tools_cache` mechanisms ([OpenAI Agents SDK](https://openai.github.io/openai-agents-python/mcp/#caching)).

The console prints the selected tool, arguments, returned result, and final response. Server receipts and errors go to stderr, leaving stdout available for MCP transport messages. Hosted SDK tracing is disabled.

## Tests

The automated suite and MCP smoke check require no model API key:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe smoke_test.py
```

Tests cover the menu, read-only lookup, exact decimal estimates, durable requests, consent, input validation, opening hours, notice periods, ambiguous matches, storage failure, retry conflicts, concurrent retries, persistence after server restart, exactly three discovered MCP tools, and the streaming Agents SDK loop. SDK loop tests use an explicit model test double; the live walkthrough separately checks real model tool selection.

If a restricted environment prevents pytest from using its default temporary directory, pass a fresh writable path with `--basetemp .pytest_tmp/run1`. Tests use isolated databases rather than the interactive request database.

## Tool contracts

- `search_menu(query="")`: a case-insensitive literal substring search over product IDs, names, descriptions, ingredients, and allergens. An empty string lists all products. No match is an empty product list; whitespace-only and oversized queries produce `INVALID_QUERY`. No database is required for this operation.
- `save_pickup_inquiry(product_id, quantity, customer, pickup_at, request_id)`: exact product ID, **1-30 integer loaves**, a typed customer object with `name`, `email`, and affirmative `consent`, an offset-aware ISO 8601 pickup time, and an **8-64 character** request reference. The result includes a unique inquiry ID, menu-price estimate, and `pending_confirmation` status. Contact details are saved for pickup follow-up; the email is not repeated in the tool result.
- `prepare_pickup_inquiry(query, quantity, customer, pickup_at, request_id)`: the same request details, with a query replacing the exact product ID. It requires exactly one match. No match or multiple matches creates no inquiry; the assistant asks for a more specific choice.

Pickup inquiries require at least **24 hours' notice**. Cedar Crumb operates **Monday-Saturday, 07:00-19:00 Beirut time**; pickup times must fall before closing. Requests await staff confirmation of availability and arrangements. The inquiry total uses published menu prices; larger quantities and additional arrangements require a staff quote.

The request reference is an idempotency key. Repeating it with identical normalized details returns the original inquiry ID and `already_saved=true`, without another write. Reusing it for different details raises `REQUEST_CONFLICT`. The saved price remains the original snapshot. A SQLite transaction and unique request key serialize competing retries.

## Project files

| File | Purpose |
| --- | --- |
| `server.py` | Exactly three FastMCP tools |
| `client.py` | Agents SDK Host/Client and live walkthrough |
| `app.py` | Browser chat, actual MCP activity, and saved inquiries |
| `session_runtime.py` | Session-owned MCP connection, request queue, and cleanup |
| `C5_Cedar_Crumb_Colab.ipynb` | Colab launcher for the source ZIP |
| `bakery.py`, `models.py` | Shared operations, validated contracts, durable storage |
| `data/bakery.json` | Cedar Crumb menu and policies |
| `tests/`, `smoke_test.py` | Unit, protocol, and SDK loop checks |
| `docs/summary.md` | Assigned reading summary and Fetch-server critique |
| `docs/reflection.md` | Tool design reflection |
| `docs/verification.md` | Verification results |
| `package_submission.py` | Builds the clean source submission ZIP |

Source packaging excludes `.env`, request databases, local results, caches, and virtual environments.

Rebuild the source archive after edits with `python package_submission.py`. The ZIP is written beside this project folder as `C5_Cedar_Crumb_Submission.zip`.

## References

- [Assigned MCP article](https://huggingface.co/blog/Kseniase/mcp)
- [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)
- [Fetch server implementation](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/src/mcp_server_fetch/server.py)
- [FastMCP tools](https://gofastmcp.com/servers/tools)
- [OpenAI Agents SDK MCP integration](https://openai.github.io/openai-agents-python/mcp/)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
