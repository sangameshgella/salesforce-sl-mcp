import sys
import logging
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response
from salesforce_client import SalesforceClient

# Initialize Salesforce Client
sf_client = SalesforceClient()
logger = logging.getLogger("mcp.sse")

# Initialize Standard MCP Server
server = Server("support-case-mcp")

@server.list_tools()
async def list_tools():
    logger.info("list_tools invoked")
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
        },
        {
            "name": "get_case_history",
            "description": "Get the history of field changes for a case. Shows what modifications were made, when, and by whom.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"]
            }
        },
        {
            "name": "get_case_timeline",
            "description": "Get the activity feed/timeline for a case. Shows posts, updates, and activities.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"]
            }
        },
        {
            "name": "get_case_summary",
            "description": "Get comprehensive case data for follow-up inquiries. Returns case info, fix status, validation status, history, and recent comments. Use this for customer follow-up questions about case status.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"]
            }
        },
        {
            "name": "suggest_knowledge_article",
            "description": "Check if a resolved case is suitable for conversion to a Knowledge Article (KBA). Returns eligibility and suggested prompt.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"]
            }
        }
    ]

@server.call_tool()
async def call_tool(name, arguments):
    logger.info("call_tool invoked: %s", name)
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

    raise ValueError(f"Tool {name} not found")

# Create SSE Transport handler
sse = SseServerTransport("/messages")

class SseEndpoint:
    async def __call__(self, scope, receive, send):
        logger.info("SSE connect: path=%s query=%s", scope.get("path"), scope.get("query_string"))
        async with sse.connect_sse(scope, receive, send) as streams:
            try:
                await server.run(streams[0], streams[1], server.create_initialization_options())
            except Exception:
                logger.exception("server.run failed")
                raise


class PostMessageEndpoint:
    async def __call__(self, scope, receive, send):
        logger.info("POST message: path=%s query=%s", scope.get("path"), scope.get("query_string"))
        await sse.handle_post_message(scope, receive, send)

# Create Starlette App (This is what Uvicorn runs)
async def handle_home(request: Request):
    return Response("MCP Server Running. Use /sse endpoint for connection.")

routes = [
    Route("/sse", endpoint=SseEndpoint(), methods=["GET"]),
    Route("/sse/", endpoint=SseEndpoint(), methods=["GET"]),
    Route("/messages", endpoint=PostMessageEndpoint(), methods=["POST"]),
    Route("/messages/", endpoint=PostMessageEndpoint(), methods=["POST"]),
    Route("/sse/messages", endpoint=PostMessageEndpoint(), methods=["POST"]),
    Route("/sse/messages/", endpoint=PostMessageEndpoint(), methods=["POST"]),
    Route("/", endpoint=handle_home),
]

mcp = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp, port=8000)
