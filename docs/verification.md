# Test results

Test environment: Python 3.12 on Windows.

## Automated checks

**63 tests passed.** The suite contains 40 domain cases, five SDK/output-handling cases, a real MCP integration scenario, and seventeen browser-host/cache checks. The browser-host tests exercise the real MCP subprocess with a deterministic model, session isolation, empty input, failure recovery, interface construction, and connection/cache lifecycle.

- The Data operation returns the established six-product menu and documented policies. Tests verify the product IDs, prices, weights, ingredient search, no-match behavior, input failures, and absence of writes.
- The Action saves an inquiry with an exact decimal estimate and `pending_confirmation` status. Tests cover consent, unknown products, invalid quantities, customer validation, offset-aware pickup dates, the 24-hour notice boundary, Sunday closure, opening hours, and inaccessible storage.
- The Orchestration operation is tested for the actual Data-then-Action call order, correct product and estimate, no-match and ambiguous-match failures, persistence, and retries.
- A unique request reference and transaction prevent duplicate writes. Eight concurrent identical requests return one inquiry ID. Changed details with an existing reference cause an explicit conflict. Equivalent timezone offsets normalize to the same pickup instant; a later retry returns the saved reference even after the original pickup date.
- The integration scenario uses the OpenAI Agents SDK's actual `MCPServerStdio` client and a separate FastMCP process. It discovers exactly three tools, checks their descriptions and output schemas, invokes all three categories, checks errors without extra records, and reconnects a new server process to verify persistence.
- Streaming SDK tests use a deterministic model test double to verify that real MCP results return to the next model input. The test double is separate from the live Gemini client.

## Interactive application and Colab launcher

- Gradio 6.31.0 interface renders the six-product menu, conversation, five recording prompts, current-request timeline, detailed MCP trace, and saved inquiry table. Each request has an activity reference linking its incoming message, actual discovery request/result, schemas passed to the model, selection/arguments, actual tools/call/result, result delivery to the agent, and final answer.
- A browser integration check completed two chat turns. The first timeline displayed discovery, schema delivery to the model, selection, actual tools/call/result, and final response. The second timeline displayed cross-turn cache reuse without another discovery request, followed by a new tools/call and result. It used a deterministic model with real MCP transport, without Gemini calls.
- The notebook contains the six application modules in code cells. Data, Action, and Orchestration each have a separate section, followed by Host/Client/Server configuration and chat. The assembled modules match the packaged Python source. Tool discovery verifies exactly three tools over a real stdio connection.
- uv 0.13.6 installs a managed Python 3.12 environment independently of the Colab kernel.
- Linux x86_64/Python 3.12 dependency resolution succeeded for all 102 packages. Hosted Colab execution has not been validated.
- `pip check` reports no broken requirements in the tested Windows environment.

## Connection and cache checks

- Two chat turns share one MCP connection and issue one real discovery request. Both tool invocations execute.
- First-turn discovery includes the three names, descriptions, input schemas, and output schemas. The observed model request contains the actual function schemas passed by the SDK.
- Reset enters and closes the real MCP context on the same owner task; a new chat discovers independently.
- Separate chats have separate connections and schema caches. A reconnect never claims a cross-turn cache hit.
- Tool-list change notifications invalidate the schema cache and force a new network discovery request.
- An idle connection closes cleanly. Active requests execute on their connection's owner task.
- Identical Action retries execute twice, return the same inquiry ID, and change `already_saved` from false to true without creating a duplicate.
- A menu-file update is visible on the next Data call while the schema cache remains active.

## Test scope

Automated tests use a deterministic model and real MCP transport. The live Gemini walkthrough is available through `client.py --demo`; its routing, row-count, price, failure, and retry assertions require a configured API key. Live Gemini execution has not been validated.
