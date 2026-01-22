import sys
import logging
from contextlib import asynccontextmanager

import mcp

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import Response

from salesforce_client import SalesforceClient

# Initialize Salesforce Client
sf_client = SalesforceClient()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp.sse")
logger.info("MCP VERSION: %s", getattr(mcp, "__version__", "unknown"))

# Initialize Standard MCP Server
server = Server("support-case-mcp")

@server.list_tools()
async def list_tools():
    logger.info("list_tools invoked")
    from mcp.types import Tool
    return [
        Tool(
            name="search",
            description="Search for support cases by keyword or phrase. Returns matching cases.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query string"}},
                "required": ["query"],
            },
        ),
        Tool(
            name="fetch",
            description="Fetch full details for a support case by case number.",
            inputSchema={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Case Number (not Id)"}},
                "required": ["id"],
            },
        ),
        Tool(
            name="get_case_details",
            description="Get full details of a support case by its Case Number (e.g., 00335943). Returns Subject, Description, Status, and Comments.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number (not Id)"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="search_cases",
            description="Search for support cases using a keyword or phrase. Returns matching cases with snippets.",
            inputSchema={
                "type": "object",
                "properties": {"query_string": {"type": "string", "description": "Keywords to search for"}},
                "required": ["query_string"],
            },
        ),
        Tool(
            name="get_case_history",
            description="Get the history of field changes for a case. Shows what modifications were made, when, and by whom.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="get_case_timeline",
            description="Get the activity feed/timeline for a case. Shows posts, updates, and activities.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="get_related_cases",
            description="Find cases related to the given case. Useful for identifying patterns or duplicate issues.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="get_case_articles",
            description="Get knowledge articles attached to or suggested for a case.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="get_case_summary",
            description="Get a comprehensive summary of a case including fix status, validation status, closure readiness, recent changes, and comments.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
        Tool(
            name="suggest_knowledge_article",
            description="Check if a resolved case is eligible to be converted into a Knowledge Article (KBA) for future reference.",
            inputSchema={
                "type": "object",
                "properties": {"case_number": {"type": "string", "description": "The Case Number"}},
                "required": ["case_number"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(name, arguments):
    logger.info("call_tool invoked: %s", name)
    if name == "search":
        query = arguments.get("query")
        results = sf_client.search_cases(query)
        if not results:
            return [{"type": "text", "text": "No cases found matching that query."}]

        output = []
        for r in results:
            output.append(f"{r['CaseNumber']}: {r['Subject']} ({r['Status']})")

        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "fetch":
        case_number = arguments.get("id")
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

    elif name == "get_case_details":
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

    elif name == "get_case_history":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        history = sf_client.get_case_history(case['Id'])
        if not history:
            return [{"type": "text", "text": f"No history found for case {case_number}."}]
        
        output = [f"Case {case_number} - Field Change History:", ""]
        for h in history:
            field = h.get('Field', 'Unknown')
            old_val = h.get('OldValue', '(empty)')
            new_val = h.get('NewValue', '(empty)')
            date = h.get('CreatedDate', '')
            user = h.get('CreatedBy', {}).get('Name', 'Unknown') if h.get('CreatedBy') else 'Unknown'
            output.append(f"[{date}] {user}: {field} changed from '{old_val}' to '{new_val}'")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_case_timeline":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        feed = sf_client.get_case_feed(case['Id'])
        if not feed:
            return [{"type": "text", "text": f"No timeline/feed found for case {case_number}."}]
        
        output = [f"Case {case_number} - Activity Timeline:", ""]
        for f in feed:
            body = f.get('Body', '(no content)')
            feed_type = f.get('Type', 'Post')
            date = f.get('CreatedDate', '')
            user = f.get('CreatedBy', {}).get('Name', 'Unknown') if f.get('CreatedBy') else 'Unknown'
            output.append(f"[{date}] [{feed_type}] {user}: {body}")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_case_summary":
        case_number = arguments.get("case_number")
        summary_data = sf_client.get_case_summary_data(case_number)
        if not summary_data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_info = summary_data['case_info']
        tech_summary = summary_data['technical_summary']
        
        output = [
            f"=== Case Summary: {case_info['CaseNumber']} ===",
            f"Subject: {case_info['Subject']}",
            f"Status: {case_info['Status']}",
            f"Priority: {case_info['Priority']}",
            f"Created: {case_info['CreatedDate']}",
            f"Last Modified: {case_info['LastModifiedDate']}",
            "",
            "--- Technical Status ---",
            f"Fix Status: {tech_summary['fix_status'] or 'Not set'}",
            f"Validation Status: {tech_summary['validation_status'] or 'Not set'}",
            f"Closure Readiness: {tech_summary['closure_readiness']}",
            "",
            "--- Description ---",
            case_info['Description'] or '(No description)',
            ""
        ]
        
        # Add recent history
        if summary_data['history']:
            output.append("--- Recent Changes ---")
            for h in summary_data['history'][:5]:
                field = h.get('Field', 'Unknown')
                new_val = h.get('NewValue', '')
                date = h.get('CreatedDate', '')
                output.append(f"  [{date}] {field} → {new_val}")
            output.append("")
        
        # Add recent comments
        if summary_data['recent_comments']:
            output.append("--- Recent Comments ---")
            for c in summary_data['recent_comments'][:3]:
                user = c.get('CreatedBy', {}).get('Name', 'Unknown') if c.get('CreatedBy') else 'Unknown'
                date = c.get('CreatedDate', '')
                body = c.get('CommentBody', '')
                output.append(f"  [{date}] {user}: {body}")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "suggest_knowledge_article":
        case_number = arguments.get("case_number")
        summary_data = sf_client.get_case_summary_data(case_number)
        if not summary_data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        tech_summary = summary_data['technical_summary']
        case_info = summary_data['case_info']
        
        # Determine KBA eligibility
        eligible = False
        reason = ""
        prompt = ""
        
        if tech_summary['closure_readiness'] == 'ready':
            eligible = True
            reason = "Resolved technical issue with documented fix and completed validation."
            prompt = f"""This resolved technical issue can be reused as a reference.
Would you like to convert this solution into a Knowledge Article for future cases?

Suggested KBA Title: {case_info['Subject']}
Case Reference: {case_info['CaseNumber']}"""
        elif tech_summary['closure_readiness'] == 'pending_validation':
            eligible = False
            reason = "Fix is implemented but validation is not yet complete."
            prompt = "Complete validation before considering KBA creation."
        else:
            eligible = False
            reason = "Case is still in progress."
            prompt = "Resolve the case before considering KBA creation."
        
        output = [
            f"=== Knowledge Article Eligibility: {case_info['CaseNumber']} ===",
            f"Eligible: {'Yes' if eligible else 'No'}",
            f"Reason: {reason}",
            "",
            prompt
        ]
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_related_cases":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        related = sf_client.get_related_cases(case['Id'], case['Subject'])
        if not related:
            return [{"type": "text", "text": f"No related cases found for {case_number}."}]
        
        output = [f"Cases related to {case_number}:", ""]
        for r in related:
            output.append(f"- [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_case_articles":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        articles = sf_client.get_case_articles(case['Id'])
        if not articles:
            return [{"type": "text", "text": f"No knowledge articles linked to case {case_number}."}]
        
        output = [f"Knowledge Articles for {case_number}:", ""]
        for a in articles:
            ka = a.get('KnowledgeArticle', {})
            output.append(f"- {ka.get('Title', 'Untitled')} ({ka.get('UrlName', '')})")
        
        return [{"type": "text", "text": "\n".join(output)}]

    raise ValueError(f"Tool {name} not found")

# Streamable HTTP transport/session manager
session_manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)


class McpEndpoint:
    async def __call__(self, scope, receive, send):
        logger.info("MCP REQUEST: %s %s", scope.get("method"), scope.get("path"))
        headers = list(scope.get("headers") or [])
        header_names = {k.lower() for k, _ in headers}
        if b"accept" not in header_names:
            headers.append((b"accept", b"application/json"))
        if scope.get("method") == "POST" and b"content-type" not in header_names:
            headers.append((b"content-type", b"application/json"))
        if headers != list(scope.get("headers") or []):
            scope = dict(scope)
            scope["headers"] = headers

        async def receive_with_log():
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                if body:
                    logger.info("REQUEST BODY: %s", body.decode(errors="replace"))
            return message

        try:
            await session_manager.handle_request(scope, receive_with_log, send)
        except Exception:
            logger.exception(
                "mcp_app error: method=%s path=%s headers=%s",
                scope.get("method"),
                scope.get("path"),
                [(k.decode(), v.decode()) for k, v in headers],
            )
            raise


@asynccontextmanager
async def lifespan(app: Starlette):
    async with session_manager.run():
        yield


# Create Starlette App (This is what Uvicorn runs)
async def handle_home(request: Request):
    return Response("MCP Server Running. Use /mcp endpoint for connection.")


routes = [
    Route("/mcp", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]),
    Route("/mcp/", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]),
    Route("/", endpoint=handle_home),
]

mcp = Starlette(routes=routes, lifespan=lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp, host="0.0.0.0", port=8000)
