# Model Context Protocol and tool design

The assigned Hugging Face article presents MCP as a response to fragmented integrations. An agent needs information and operations outside its training data, but separate connectors for every application and service are costly to maintain. MCP standardizes how compatible applications discover capabilities, provide arguments, and receive results. The linked full article also distinguishes integration from orchestration: the protocol carries requests and context, while the agent decides which operation advances the user's goal. Useful descriptions, access controls, and monitoring still matter; a common interface cannot compensate for unreliable tool behavior. [1, 2]

MCP separates three roles. The Host manages the model, conversation, and access to capabilities. A Client inside that Host communicates with a particular Server. The Server publishes capabilities and executes their implementations. In Cedar Crumb, the OpenAI Agents SDK application is the Host, `MCPServerStdio` provides its Client connection, and the separate FastMCP process is the Server. Tool discovery supplies descriptions and schemas before the model selects a call. The result returns through the Client to the Host and becomes context for the model's answer. MCP uses JSON-RPC messages; stdio supports local processes, while Streamable HTTP supports remote connections. Resources provide context, and prompts provide reusable interaction templates. [3]

I explored the reference Fetch server's README and Python implementation. Its `fetch` tool accepts a URL, output-length limit, starting offset, and raw-output option. Retrieving content and presenting HTML as Markdown serve one focused goal: make a webpage readable to a model. Typed arguments and documented constraints make the interface clear. Separate helpers implement retrieval, extraction, and access checks. Expected network and access failures produce explicit errors. Pagination limits individual outputs, although repeated requests can observe changes to the remote page. [4, 5]

Against the class principles, Fetch has isolated functionality and a well-defined interface, with predictability limited by changing websites. Its raw-output option adds a modest mode choice; separating modes would become useful if their contracts diverged. I would replace the description's broad claim about granting internet access with a precise capability statement. Cedar Crumb applies these lessons by separating menu retrieval from inquiry persistence, documenting typed outputs and failures, and assigning their coordination to one named workflow. The Action saves a request; the Orchestration tool determines the matching product before invoking that same Action. [5, 6]

## References

1. Ksenia Se, [What Is MCP, and Why Is Everyone Talking About It?](https://huggingface.co/blog/Kseniase/mcp), Hugging Face, March 17, 2025.
2. Ksenia Se, [What Is MCP? Model Context Protocol in Agentic AI, Explained](https://www.turingpost.com/p/mcp), the full article linked from the assigned reading.
3. Model Context Protocol, [Architecture overview](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture).
4. MCP reference servers, [Fetch README](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch).
5. MCP reference servers, [Fetch implementation](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/src/mcp_server_fetch/server.py).
6. *C5 - Agent Tools*, course notes, pp. 2-3, and the lecture deck.

