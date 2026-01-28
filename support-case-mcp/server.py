import sys
import logging
import time
import json
import asyncio
from contextlib import asynccontextmanager

import mcp

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
from starlette.responses import Response

from salesforce_client import SalesforceClient

# Configure logging for Railway (stdout with immediate flush)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
# Force flush after each log
for handler in logging.root.handlers:
    handler.flush = lambda: sys.stdout.flush()

logger = logging.getLogger("mcp.sse")

# Also configure salesforce_client logger to use same settings
sf_logger = logging.getLogger("salesforce_client")
sf_logger.setLevel(logging.DEBUG)

# Initialize Salesforce Client
sf_client = SalesforceClient()
logger.info("MCP VERSION: %s", getattr(mcp, "__version__", "unknown"))

def _debug_log(payload: dict) -> None:
    try:
        logger.info("DEBUG_LOG %s", payload)
    except Exception:
        pass

# Initialize Standard MCP Server
server = Server("support-case-mcp")

@server.list_tools()
async def list_tools():
    logger.info("list_tools invoked")
    from mcp.types import Tool
    return [
        Tool(
            name="case_flow_summary",
            description="Return a structured JSON summary that covers status review, resolution check, customer communication, reusable outputs, and a visual flow tree. ALWAYS pulls fresh data from Salesforce.",
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
        # ========== SCHEMA TOOLS (Call before any write operation) ==========
        Tool(
            name="describe_sobject",
            description="Get field metadata for a Salesforce object. MUST call this BEFORE any update operation to know valid field names, data types, and picklist values. Returns field API names, labels, types, and valid picklist options with both display labels and API values. Use this to map user-friendly terms (like 'closed') to exact API values (like 'Closed').",
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Salesforce object API name (e.g., 'Case', 'CaseComment')"}
                },
                "required": ["object_name"],
            },
        ),
        Tool(
            name="describe_workflow_objects",
            description="Get field metadata for ALL objects in the support case workflow (Case, CaseComment, Knowledge). Call this at the start of a session or before complex multi-object operations to understand all available fields and valid values across the workflow.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ========== CASE WRITE TOOLS ==========
        Tool(
            name="update_case",
            description="Update case fields. MUST call describe_sobject('Case') first to get valid field names and picklist values. Returns success status AND contextual next_actions - after closing a case, suggests creating a KBA and documenting the closure. Always present these next actions to the user.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number to update"},
                    "fields": {"type": "object", "description": "Dictionary of field API names and values to update. Example: {\"Status\": \"Closed\"}"}
                },
                "required": ["case_number", "fields"],
            },
        ),
        Tool(
            name="add_case_comment",
            description="Add a comment to a case. Use for internal notes (is_public=false) or customer-visible responses (is_public=true). Returns success status AND contextual next_actions suggesting follow-up steps like updating status or creating a KBA.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"},
                    "comment": {"type": "string", "description": "The comment text"},
                    "is_public": {"type": "boolean", "description": "True = visible to customer, False = internal only", "default": False}
                },
                "required": ["case_number", "comment"],
            },
        ),
        # ========== KNOWLEDGE ARTICLE TOOLS ==========
        Tool(
            name="create_knowledge_article",
            description="Create a Knowledge Article from a resolved case. Creates article in Draft status and links to case. Returns success AND next_actions - often indicates 'workflow complete' since KBA creation is typically the final step. If linked to a closed case, no further actions may be needed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Article title"},
                    "summary": {"type": "string", "description": "Brief summary/abstract"},
                    "content": {"type": "string", "description": "Full article content/details"},
                    "case_number": {"type": "string", "description": "Optional - link article to this case"}
                },
                "required": ["title", "summary", "content"],
            },
        ),
    ]

def _add_suggestions(text: str, suggestions: list) -> list:
    """Helper to format response with suggested next actions"""
    suggestion_text = "\n\n💡 SUGGESTED NEXT ACTIONS:\n" + "\n".join([
        f"  • {s['tool']}: {s['reason']}" for s in suggestions
    ])
    return [{"type": "text", "text": text + suggestion_text}]

def _closure_readiness_flags(tech: dict) -> dict:
    closure_readiness = tech.get("closure_readiness", "in_progress")
    return {
        "closure_readiness": closure_readiness,
        "ready_for_closure": closure_readiness == "ready",
        "fix_status": tech.get("fix_status") or "Not set",
        "validation_status": tech.get("validation_status") or "Not set"
    }

def _build_customer_comms(case_info: dict, tech: dict) -> dict:
    closure_readiness = tech.get("closure_readiness", "in_progress")
    if closure_readiness == "ready":
        update = (
            "Fix and validation are complete. Please confirm resolution so we can close the case."
        )
        next_actions = [
            "Confirm monitoring complete",
            "Ask customer to verify resolution",
            "Close case when confirmed",
            "Create knowledge article if applicable"
        ]
        troubleshooting = []
    elif closure_readiness == "pending_validation":
        update = (
            "Fix is implemented and validation is in progress. We will update once testing completes."
        )
        next_actions = [
            "Complete validation testing",
            "Share test results and timeline",
            "Confirm with customer after validation"
        ]
        troubleshooting = ["Gather validation logs and test evidence"]
    else:
        update = (
            f"Case is in progress (status: {case_info.get('Status', 'Unknown')}). "
            "We are investigating and will provide updates."
        )
        next_actions = [
            "Continue investigation",
            "Provide interim status update",
            "Collect additional diagnostics if needed"
        ]
        troubleshooting = ["Review recent errors, logs, and reproduction steps"]

    return {
        "concise_update": update,
        "troubleshooting_steps": troubleshooting,
        "next_actions": next_actions
    }

def _build_kba_prompt(tech: dict) -> dict:
    closure_readiness = tech.get("closure_readiness", "in_progress")
    if closure_readiness == "ready":
        return {
            "eligible": True,
            "prompt": "Would you like to convert this validated solution into a Knowledge Article?"
        }
    if closure_readiness == "pending_validation":
        return {
            "eligible": False,
            "prompt": "Validation is pending. Create a Knowledge Article after validation completes."
        }
    return {
        "eligible": False,
        "prompt": "Case is still in progress. Consider a Knowledge Article after resolution."
    }

def _build_flow_tree(case_info: dict, tech: dict, metrics: dict) -> list:
    status = (case_info.get("Status") or "").lower()
    closed = status in ["closed", "resolved"]
    fix_status = (tech.get("fix_status") or "").lower()
    validation_status = (tech.get("validation_status") or "").lower()
    closure_ready = tech.get("closure_readiness") == "ready"
    has_recent_activity = metrics.get("has_recent_activity")

    fix_complete = fix_status == "implemented"
    test_complete = validation_status == "completed"

    nodes = [
        {"id": "identified", "label": "Identified", "status": "complete"},
        {"id": "fix", "label": "Fix", "status": "complete" if fix_complete else "current"},
        {
            "id": "test",
            "label": "Test",
            "status": "complete" if test_complete else ("current" if fix_complete else "pending")
        },
        {
            "id": "monitor",
            "label": "Monitor",
            "status": "current" if closure_ready and not closed else ("complete" if closed else "pending"),
            "details": {
                "has_recent_activity": has_recent_activity,
                "days_since_update": metrics.get("days_since_update")
            }
        },
        {
            "id": "closure",
            "label": "Closure",
            "status": "complete" if closed else ("current" if closure_ready else "pending")
        }
    ]
    return nodes

@server.call_tool()
async def call_tool(name, arguments):
    logger.info("call_tool invoked: %s", name)
    if name == "case_flow_summary":
        case_number = arguments.get("case_number")
        data = await asyncio.to_thread(sf_client.get_comprehensive_case_data, case_number, "full")
        if not data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_info = data.get("case_info", {})
        tech = data.get("technical_summary", {})
        metrics = data.get("metrics", {})
        history = data.get("history", [])
        comments = data.get("recent_comments", [])
        feed_items = data.get("feed_items", [])
        emails = data.get("emails", [])
        related_cases = data.get("related_cases", [])
        knowledge_articles = await asyncio.to_thread(
            sf_client.search_knowledge_articles,
            case_info.get("Subject", ""),
            case_info.get("Description", "")
        )
        risk_factors = data.get("risk_factors", [])
        
        def _snippet(text: str, limit: int = 300) -> str:
            if not text:
                return ""
            clean = text.replace("\n", " ").strip()
            if len(clean) > limit:
                return clean[:limit] + "..."
            return clean
        
        email_summaries = []
        for e in emails[:10]:
            body = e.get("TextBody") or e.get("HtmlBody") or ""
            email_summaries.append({
                "subject": e.get("Subject"),
                "from": e.get("FromAddress"),
                "to": e.get("ToAddress"),
                "cc": e.get("CcAddress"),
                "date": e.get("MessageDate") or e.get("CreatedDate"),
                "incoming": e.get("Incoming"),
                "body_snippet": _snippet(body, 400)
            })
        
        closure_flags = _closure_readiness_flags(tech)
        recurrence_signals = []
        if metrics.get("related_cases_count", 0) > 0:
            recurrence_signals.append("Related cases exist")
        for rf in risk_factors:
            if "similar cases" in rf.lower():
                recurrence_signals.append(rf)
        recurrence_risk = len(recurrence_signals) > 0
        
        status_review = {
            "case_info": case_info,
            "history": history,
            "recent_comments": comments,
            "feed_items": feed_items,
            "emails": email_summaries,
            "knowledge_articles": knowledge_articles,
            "related_cases": related_cases,
            "prior_ai_context": {
                "available": False,
                "notes": "No stored AI context found in case data."
            },
            "fix_details": {
                "fix_status": tech.get("fix_status"),
                "validation_status": tech.get("validation_status")
            },
            "testing_results": {
                "validation_status": tech.get("validation_status"),
                "ready_for_closure": closure_flags["ready_for_closure"]
            },
            "monitoring_data": {
                "days_since_update": metrics.get("days_since_update"),
                "has_recent_activity": metrics.get("has_recent_activity"),
                "risk_factors": risk_factors
            }
        }
        
        resolution_check = {
            **closure_flags,
            "recurrence_risk": recurrence_risk,
            "recurrence_signals": recurrence_signals
        }
        
        customer_communication = _build_customer_comms(case_info, tech)
        
        reusable_outputs = {
            "kba_prompt": _build_kba_prompt(tech),
            "level1_qa": [
                {
                    "question": "What is the current status of this case?",
                    "answer": f"{case_info.get('Status', 'Unknown')} (closure readiness: {closure_flags['closure_readiness']})"
                },
                {
                    "question": "What has been done so far?",
                    "answer": f"Fix status: {closure_flags['fix_status']}; Validation: {closure_flags['validation_status']}."
                }
            ],
            "level2_qa": [
                {
                    "question": "Is there evidence of recurrence or related incidents?",
                    "answer": "Yes. " + "; ".join(recurrence_signals) if recurrence_risk else "No related incidents detected."
                },
                {
                    "question": "What is the summary of the issue?",
                    "answer": _snippet(case_info.get("Description", ""), 500) or "No description available."
                }
            ]
        }
        
        visual_aid = {
            "issue_flow_tree": _build_flow_tree(case_info, tech, metrics)
        }
        
        response = {
            "data_source": "salesforce",
            "case_number": case_info.get("CaseNumber"),
            "status_review": status_review,
            "resolution_check": resolution_check,
            "customer_communication": customer_communication,
            "reusable_outputs": reusable_outputs,
            "visual_aid": visual_aid
        }
        
        return [{"type": "text", "text": json.dumps(response, indent=2)}]

    elif name == "suggest_knowledge_article":
        case_number = arguments.get("case_number")
        summary_data = await asyncio.to_thread(sf_client.get_case_summary_data, case_number)
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

    # === AGENTIC TOOL HANDLERS ===

    # ========== SCHEMA TOOL HANDLERS ==========
    
    elif name == "describe_sobject":
        object_name = arguments.get("object_name")
        if not object_name:
            return [{"type": "text", "text": "Error: object_name is required."}]
        
        result = await asyncio.to_thread(sf_client.describe_sobject, object_name)
        
        if 'error' in result:
            return [{"type": "text", "text": f"Error describing {object_name}: {result['error']}"}]
        
        # Format output for LLM consumption
        output = [
            f"=== {result['label']} ({object_name}) Schema ===",
            f"Updateable: {result['updateable']} | Createable: {result['createable']}",
            f"Total Fields: {result['field_count']}",
            "",
            "--- Updateable Fields (most relevant for updates) ---"
        ]
        
        # Show updateable fields with picklist values
        updateable_fields = [f for f in result['fields'] if f['updateable']]
        for field in updateable_fields[:30]:  # Limit to 30 most relevant
            field_line = f"  {field['api_name']} ({field['type']})"
            if field['required']:
                field_line += " [REQUIRED]"
            output.append(field_line)
            output.append(f"    Label: {field['label']}")
            
            # Show picklist values
            if 'picklist_values' in field and field['picklist_values']:
                output.append("    Valid values:")
                for pv in field['picklist_values'][:10]:  # Limit picklist display
                    default_marker = " [default]" if pv.get('default') else ""
                    output.append(f"      - \"{pv['api_value']}\" (label: {pv['label']}){default_marker}")
        
        output.append("")
        output.append("Use exact api_value strings when calling update_case.")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "describe_workflow_objects":
        result = await asyncio.to_thread(sf_client.describe_workflow_objects)
        
        output = [
            "=== Support Case Workflow Objects ===",
            ""
        ]
        
        for obj_name, obj_data in result.items():
            if 'error' in obj_data:
                output.append(f"{obj_name}: {obj_data['error']}")
            else:
                updateable_count = len([f for f in obj_data.get('fields', []) if f.get('updateable')])
                output.append(f"{obj_name}: {obj_data.get('field_count', 0)} fields ({updateable_count} updateable)")
        
        output.append("")
        output.append("Use describe_sobject for detailed field info on any object.")
        
        return [{"type": "text", "text": "\n".join(output)}]

    # ========== CASE WRITE TOOL HANDLERS ==========
    
    elif name == "update_case":
        case_number = arguments.get("case_number")
        fields = arguments.get("fields")
        
        # region agent log
        _debug_log({
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": "H4",
            "location": "server.py:update_case",
            "message": "update_case args received",
            "data": {
                "case_number_present": bool(case_number),
                "fields_type": type(fields).__name__,
                "fields_keys": list(fields.keys()) if isinstance(fields, dict) else None
            },
            "timestamp": int(time.time() * 1000)
        })
        # endregion
        
        if not fields or not isinstance(fields, dict):
            return [{"type": "text", "text": "Error: fields must be a dictionary of field names and values."}]
        
        # region agent log
        _debug_log({
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": "H5",
            "location": "server.py:update_case",
            "message": "update_case validated fields",
            "data": {"case_number": case_number, "fields_count": len(fields)},
            "timestamp": int(time.time() * 1000)
        })
        # endregion
        
        result = await asyncio.to_thread(sf_client.update_case, case_number, fields)
        
        # region agent log
        _debug_log({
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": "H6",
            "location": "server.py:update_case",
            "message": "update_case result",
            "data": {"success": result.get("success"), "error": result.get("error")},
            "timestamp": int(time.time() * 1000)
        })
        # endregion
        
        if not result['success']:
            return [{"type": "text", "text": f"Error updating case: {result['error']}"}]
        
        output = [
            "✅ CASE UPDATED SUCCESSFULLY",
            "",
            f"Case: {result['case_number']}",
            "Updated fields:"
        ]
        for field, value in result['new_values'].items():
            output.append(f"  • {field}: {value}")
        
        output.append("")
        output.append("💡 SUGGESTED: Add a comment to document this change.")

        status_value = None
        if isinstance(fields, dict):
            if "Status" in fields:
                status_value = fields.get("Status")
            else:
                for key in fields.keys():
                    if str(key).strip().lower() == "status":
                        status_value = fields.get(key)
                        break
        if status_value is not None:
            normalized_status = str(status_value).strip().lower()
            kba_status_keywords = ["done", "completed", "complete", "resolved", "closed"]
            if any(keyword in normalized_status for keyword in kba_status_keywords):
                output.append("")
                output.append("📝 KNOWLEDGE ARTICLE")
                output.append("Would you like to create a Knowledge Article for this resolved case?")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "add_case_comment":
        case_number = arguments.get("case_number")
        comment = arguments.get("comment")
        is_public = arguments.get("is_public", False)
        
        if not comment:
            return [{"type": "text", "text": "Error: comment is required."}]
        
        result = await asyncio.to_thread(sf_client.add_case_comment, case_number, comment, is_public)
        
        if not result['success']:
            return [{"type": "text", "text": f"Error adding comment: {result['error']}"}]
        
        visibility = "Public (visible to customer)" if is_public else "Internal (team only)"
        output = [
            "✅ COMMENT ADDED",
            "",
            f"Case: {result['case_number']}",
            f"Visibility: {visibility}",
            f"Comment ID: {result['comment_id']}",
        ]
        
        return [{"type": "text", "text": "\n".join(output)}]

    # ========== KNOWLEDGE ARTICLE TOOL HANDLERS ==========
    
    elif name == "create_knowledge_article":
        title = arguments.get("title")
        summary = arguments.get("summary")
        content = arguments.get("content")
        case_number = arguments.get("case_number")
        
        if not all([title, summary, content]):
            return [{"type": "text", "text": "Error: title, summary, and content are required."}]
        
        result = await asyncio.to_thread(
            lambda: sf_client.create_knowledge_article(title, summary, content, case_number=case_number)
        )
        
        if not result['success']:
            return [{"type": "text", "text": f"Error creating article: {result['error']}"}]
        
        output = [
            "✅ KNOWLEDGE ARTICLE CREATED",
            "",
            f"Title: {result['title']}",
            f"Article ID: {result['article_id']}",
            f"URL Name: {result['url_name']}",
            f"Status: {result['status']}",
        ]
        
        if result.get('linked_case'):
            output.append(f"Linked to case: {result['linked_case']}")
        
        output.append("")
        output.append("Note: Article is in Draft status. Publish it in Salesforce to make it available.")
        
        return [{"type": "text", "text": "\n".join(output)}]

    raise ValueError(f"Tool {name} not found")

# Streamable HTTP transport/session manager
session_manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True)


class McpEndpoint:
    async def __call__(self, scope, receive, send):
        logger.info("MCP REQUEST: %s %s", scope.get("method"), scope.get("path"))
        # region agent log
        _debug_log({
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": "H2",
            "location": "server.py:McpEndpoint",
            "message": "MCP endpoint hit",
            "data": {"method": scope.get("method"), "path": scope.get("path")},
            "timestamp": int(time.time() * 1000)
        })
        # endregion
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
                    # region agent log
                    _debug_log({
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "H3",
                        "location": "server.py:receive_with_log",
                        "message": "MCP request body received",
                        "data": {"body_preview": body[:200].decode(errors="replace")},
                        "timestamp": int(time.time() * 1000)
                    })
                    # endregion
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


async def handle_not_found(request: Request):
    # region agent log
    _debug_log({
        "sessionId": "debug-session",
        "runId": "run1",
        "hypothesisId": "H1",
        "location": "server.py:handle_not_found",
        "message": "Non-MCP path hit",
        "data": {"method": request.method, "path": request.url.path},
        "timestamp": int(time.time() * 1000)
    })
    # endregion
    return Response("Not Found", status_code=404)


routes = [
    Route("/mcp", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]),
    Route("/mcp/", endpoint=McpEndpoint(), methods=["GET", "POST", "DELETE"]),
    Route("/", endpoint=handle_home),
    Route("/{path:path}", endpoint=handle_not_found),
]

mcp = Starlette(routes=routes, lifespan=lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp, host="0.0.0.0", port=8000)
