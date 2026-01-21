import sys
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response
from salesforce_client import SalesforceClient

# Initialize Salesforce Client
sf_client = SalesforceClient()

# Initialize Standard MCP Server
server = Server("support-case-mcp")

@server.list_tools()
async def list_tools():
    return [
        {
            "name": "get_case_details",
            "description": "Get full details of a support case by its Case Number (e.g., 00335943). Returns Subject, Description, Status, and Comments.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number (not Id)"}
                },
                "required": ["case_number"]
            }
        },
        {
            "name": "search_cases",
            "description": "Search for support cases using a keyword or phrase. Returns matching cases with snippets.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query_string": {"type": "string", "description": "Keywords to search for"}
                },
                "required": ["query_string"]
            }
        }
    ]

@server.call_tool()
async def call_tool(name, arguments):
    if name == "get_case_details":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        comments = sf_client.get_case_comments(case['Id'])
        output = [
            f"Case: {case['CaseNumber']}",
            f"Subject: {case['Subject']}",
            f"Status: {case['Status']}",
            f"Priority: {case['Priority']}",
            f"Description: {case['Description']}",
            "\n--- Recent Comments ---"
        ]
        for c in comments:
            output.append(f"[{c['CreatedDate']}] {c['CreatedBy']['Name']}: {c['CommentBody']}")
            
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "search_cases":
        query = arguments.get("query_string")
        results = sf_client.search_cases(query)
        if not results:
            return [{"type": "text", "text": "No cases found matching that query."}]
        
        output = [f"Found {len(results)} cases:"]
        for r in results:
            desc_snippet = (r.get('Description') or "")[:100].replace('\n', ' ')
            if len(r.get('Description') or "") > 100:
                desc_snippet += "..."
            output.append(f"- [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
            if desc_snippet:
                 output.append(f"  Snippet: {desc_snippet}")
        
        return [{"type": "text", "text": "\n".join(output)}]

    raise ValueError(f"Tool {name} not found")

# Create SSE Transport handler
sse = SseServerTransport("/messages")

async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())

# Create Starlette App (This is what Uvicorn runs)
async def handle_home(request: Request):
    return Response("MCP Server Running. Use /sse endpoint for connection.")

routes = [
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_sse, methods=["POST"]),
    Route("/", endpoint=handle_home)
]

mcp = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp, port=8000)
