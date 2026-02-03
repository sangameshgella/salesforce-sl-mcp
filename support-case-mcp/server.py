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
    tools = [
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
        Tool(
            name="case_level2_qa",
            description="Return Level 2 Q&A for a case when explicitly requested.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"],
            },
        ),
    ]
    logger.info("list_tools response: %s", [t.name for t in tools])
    return tools

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

def _build_flowchart_mermaid(flow_nodes: list) -> str:
    lines = ["graph TD"]
    for node in flow_nodes:
        node_id = node.get("id")
        if not node_id:
            continue
        label = node.get("label") or node_id.replace("_", " ").title()
        status = node.get("status") or "pending"
        if status not in {"complete", "current", "pending"}:
            status = "pending"
        safe_label = str(label).replace('"', '\\"')
        lines.append(f'    {node_id}["{safe_label}\\n({status})"]')

    for index in range(len(flow_nodes) - 1):
        from_id = flow_nodes[index].get("id")
        to_id = flow_nodes[index + 1].get("id")
        if from_id and to_id:
            lines.append(f"    {from_id} --> {to_id}")

    return "\n".join(lines)

def _snippet(text: str, limit: int = 300) -> str:
    if not text:
        return ""
    clean = text.replace("\n", " ").strip()
    if len(clean) > limit:
        return clean[:limit] + "..."
    return clean

@server.call_tool()
async def call_tool(name, arguments):
    logger.info("call_tool invoked: %s", name)
    if name == "case_flow_summary":
        case_number = arguments.get("case_number")
        case_record = await asyncio.to_thread(sf_client.get_case_with_status, case_number)
        if not case_record:
            candidates = await asyncio.to_thread(sf_client.search_cases, case_number or "")
            candidates_list = []
            for c in candidates[:10]:
                candidates_list.append({
                    "case_number": c.get("CaseNumber"),
                    "subject": c.get("Subject"),
                    "status": c.get("Status")
                })
            response = {
                "case_found": False,
                "message": "No exact case number match. Select one of the candidates and re-run case_flow_summary with that case_number.",
                "candidates": candidates_list
            }
            return [{"type": "text", "text": json.dumps(response, indent=2)}]
        
        case_id = case_record["Id"]
        subject = case_record.get("Subject", "Unknown subject")
        description = case_record.get("Description", "")
        status = case_record.get("Status", "Unknown")
        case_summary_ai = case_record.get("Case_Summary_AI__c") or ""
        
        history_task = asyncio.to_thread(sf_client.get_case_history, case_id)
        comments_task = asyncio.to_thread(sf_client.get_case_comments, case_id)
        feed_task = asyncio.to_thread(sf_client.get_case_feed, case_id)
        emails_task = asyncio.to_thread(sf_client.get_case_emails, case_id)
        related_task = asyncio.to_thread(sf_client.get_related_cases, case_id, subject)
        articles_task = asyncio.to_thread(sf_client.get_case_articles, case_id)
        search_articles_task = asyncio.to_thread(
            sf_client.search_knowledge_articles,
            subject,
            description
        )
        
        history, comments, feed_items, emails, related_cases, case_articles, knowledge_articles = await asyncio.gather(
            history_task,
            comments_task,
            feed_task,
            emails_task,
            related_task,
            articles_task,
            search_articles_task
        )
        
        case_info = {
            "CaseNumber": case_record.get("CaseNumber"),
            "Subject": subject,
            "Description": description,
            "Status": status,
            "Priority": case_record.get("Priority"),
            "CreatedDate": case_record.get("CreatedDate"),
            "LastModifiedDate": case_record.get("LastModifiedDate"),
            "ContactName": case_record.get("Contact", {}).get("Name") if case_record.get("Contact") else None
        }
        
        fix_status = case_record.get("Fix_Status__c", "") or ""
        validation_status = case_record.get("Validation_Status__c", "") or ""
        if fix_status == "Implemented" and validation_status == "Completed":
            closure_readiness = "ready"
        elif fix_status == "Implemented":
            closure_readiness = "pending_validation"
        else:
            closure_readiness = "in_progress"
        tech = {
            "fix_status": fix_status,
            "validation_status": validation_status,
            "closure_readiness": closure_readiness
        }
        
        from datetime import datetime
        last_modified = case_record.get("LastModifiedDate", "")
        days_since_update = None
        if last_modified:
            try:
                lm_date = datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
                days_since_update = (datetime.now(lm_date.tzinfo) - lm_date).days
            except Exception:
                pass
        
        metrics = {
            "total_comments": len(comments),
            "total_history_changes": len(history),
            "related_cases_count": len(related_cases),
            "articles_count": len(case_articles),
            "has_recent_activity": days_since_update is not None and days_since_update < 7,
            "days_since_update": days_since_update,
            "emails_count": len(emails)
        }
        
        risk_factors = []
        if days_since_update and days_since_update > 14:
            risk_factors.append("No activity for 14+ days")
        if len(comments) == 0:
            risk_factors.append("No comments on case")
        
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
        
        activity_bits = []
        if feed_items:
            activity_bits.append(_snippet(feed_items[0].get("Body", ""), 140))
        if history:
            first_history = history[0]
            history_text = f"{first_history.get('Field', 'Field')} changed from {first_history.get('OldValue')} to {first_history.get('NewValue')}"
            activity_bits.append(_snippet(history_text, 140))
        if comments:
            activity_bits.append(_snippet(comments[0].get("CommentBody", ""), 140))
        if email_summaries:
            activity_bits.append(_snippet(email_summaries[0].get("body_snippet", ""), 140))
        
        case_summary_context = {
            "case_info": case_info,
            "case_summary_ai": case_summary_ai,
            "recent_activity_snippets": activity_bits[:3],
            "recent_history": history[:3],
            "recent_comments": comments[:3],
            "recent_feed": feed_items[:3],
            "recent_emails": email_summaries[:3],
            "metrics": metrics
        }
        case_summary_prompt = (
            "Using the fields in the case_summary_context JSON, write 2 to 3 concise sentences summarizing the case for an "
            "internal agent. Output only sentences, no bullets."
        )
        
        combined_text = f"{subject} {description}".lower()
        issue_parts = []
        if "firmware" in combined_text:
            issue_parts.append("Firmware")
        if "sdk" in combined_text:
            issue_parts.append("SDK runtime behavior")
        issue_type = " / ".join(issue_parts) if issue_parts else "General case issue"
        
        if closure_flags["closure_readiness"] == "ready":
            current_state = "Monitoring ongoing"
            closure_dependency = "Stable behavior confirmation"
        elif closure_flags["closure_readiness"] == "pending_validation":
            current_state = "Validation in progress"
            closure_dependency = "Validation completion"
        else:
            current_state = "Fix in progress"
            closure_dependency = "Fix implementation and validation"
        
        if case_summary_ai:
            current_state = _snippet(case_summary_ai, 200)
        
        technical_summary = {
            "issue_type": issue_type,
            "fix_status": closure_flags["fix_status"],
            "validation_status": closure_flags["validation_status"],
            "current_state": current_state,
            "closure_dependency": closure_dependency
        }
        
        troubleshooting_recommendations = []
        for article in knowledge_articles[:5]:
            troubleshooting_recommendations.append({
                "type": "knowledge_article",
                "id": article.get("id"),
                "title": article.get("title"),
                "url_name": article.get("url_name"),
                "summary": _snippet(article.get("summary"), 220)
            })
        for article in case_articles[:3]:
            troubleshooting_recommendations.append({
                "type": "linked_knowledge_article",
                "id": article.get("KnowledgeArticleId"),
                "title": article.get("Title"),
                "url_name": article.get("UrlName"),
                "summary": _snippet(article.get("Summary"), 220)
            })
        for related in related_cases[:3]:
            troubleshooting_recommendations.append({
                "type": "related_case",
                "case_number": related.get("CaseNumber"),
                "subject": related.get("Subject"),
                "status": related.get("Status")
            })
        
        actions = []
        if closure_flags["fix_status"].lower() != "implemented":
            actions.append("Confirm fix plan and update Fix Status.")
        if closure_flags["fix_status"].lower() == "implemented" and closure_flags["validation_status"].lower() != "completed":
            actions.append("Complete validation and update Validation Status.")
        if closure_flags["closure_readiness"] == "ready" and status.lower() not in ["closed", "resolved"]:
            actions.append("Confirm monitoring stability and close the case.")
        if knowledge_articles:
            actions.append("Review existing knowledge articles before creating a new one.")
        if not knowledge_articles and closure_flags["closure_readiness"] == "ready":
            actions.append("Create a Knowledge Article from the resolution.")
        if recurrence_risk:
            actions.append("Review related cases to confirm recurrence patterns.")
        if not actions:
            actions.append("Add a brief internal update summarizing progress.")
        
        flow_nodes = _build_flow_tree(case_info, tech, metrics)
        visual_tree = {
            "mermaid": _build_flowchart_mermaid(flow_nodes),
            "bullets": [
                "Existing Technical Issue",
                "Issue Identified",
                "Fix Implemented",
                "Testing Completed",
                "Monitoring",
                "Case Closure"
            ]
        }
        
        response_format_prompt = (
            "Using the provided JSON, output the response in this order and format:\n"
            "1. Case Summarization & Contextualization: 2 to 3 sentence paragraph.\n"
            "2. Technical Case Summary: labeled lines (Issue Type, Fix Status, Validation Status, "
            "Current State, Closure Dependency).\n"
            "3. Troubleshooting / Resolution Recommendation Steps: list of articles/cases.\n"
            "4. Action: bulleted actions.\n"
            "Do not ask questions or request missing inputs. Use only the provided JSON.\n"
            "Output only these sections and keep them concise."
        )

        response_format_context = {
            "case_summary_context": case_summary_context,
            "case_summary_prompt": case_summary_prompt,
            "technical_summary": technical_summary,
            "troubleshooting_recommendations": troubleshooting_recommendations,
            "actions": actions,
            "visual_tree": visual_tree
        }

        response = {
            "response_format_prompt": response_format_prompt,
            "response_format_context": response_format_context
        }
        
        return [{"type": "text", "text": json.dumps(response, indent=2)}]

    elif name == "case_level2_qa":
        case_number = arguments.get("case_number")
        case_record = await asyncio.to_thread(sf_client.get_case_with_status, case_number)
        if not case_record:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_summary_ai = case_record.get("Case_Summary_AI__c") or ""
        fix_status = (case_record.get("Fix_Status__c") or "").lower()
        validation_status = (case_record.get("Validation_Status__c") or "").lower()
        monitoring_phrase = "monitoring is ongoing"
        if fix_status != "implemented":
            monitoring_phrase = "monitoring will begin after implementation"
        
        answer_suffix = f" Case summary: {_snippet(case_summary_ai, 220)}" if case_summary_ai else ""
        
        level2_qa = [
            {
                "question": "Why is this case still open if the fix is already implemented?",
                "answer": f"The case remains open to ensure post-implementation stability through monitoring. {answer_suffix}".strip()
            },
            {
                "question": "What was done to validate the fix?",
                "answer": (
                    f"Testing was completed after implementation and {monitoring_phrase}. "
                    f"{answer_suffix}".strip()
                )
            }
        ]
        
        if validation_status != "completed":
            level2_qa[1]["answer"] = (
                f"Validation is still in progress and {monitoring_phrase}. {answer_suffix}".strip()
            )
        
        return [{"type": "text", "text": json.dumps({"level2_qa": level2_qa}, indent=2)}]

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
        method = scope.get("method")
        if method in ("OPTIONS", "HEAD"):
            await Response("", status_code=204)(scope, receive, send)
            return
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
                    logger.info(
                        "REQUEST BODY (preview, %s bytes): %s",
                        len(body),
                        body[:2000].decode(errors="replace")
                    )
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
        
        response_body = bytearray()
        response_body_limit = 2000

        async def send_with_log(message):
            if message.get("type") == "http.response.start":
                status = message.get("status")
                logger.info("MCP RESPONSE STATUS: %s %s %s", status, method, scope.get("path"))
            if message.get("type") == "http.response.body":
                body = message.get("body") or b""
                if body and len(response_body) < response_body_limit:
                    remaining = response_body_limit - len(response_body)
                    response_body.extend(body[:remaining])
                if not message.get("more_body"):
                    logger.info(
                        "RESPONSE BODY (preview, %s bytes): %s",
                        len(response_body),
                        response_body.decode(errors="replace")
                    )
            await send(message)

        start_time = time.time()
        try:
            if method == "GET":
                await session_manager.handle_request(scope, receive_with_log, send_with_log)
            else:
                await asyncio.wait_for(
                    session_manager.handle_request(scope, receive_with_log, send_with_log),
                    timeout=25.0
                )
        except asyncio.TimeoutError:
            logger.error(
                "MCP request timed out: method=%s path=%s",
                scope.get("method"),
                scope.get("path")
            )
            await Response("MCP request timed out", status_code=504)(scope, receive, send)
        except Exception:
            logger.exception(
                "mcp_app error: method=%s path=%s headers=%s",
                scope.get("method"),
                scope.get("path"),
                [(k.decode(), v.decode()) for k, v in headers],
            )
            raise
        finally:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "MCP request completed in %sms: %s %s",
                duration_ms,
                scope.get("method"),
                scope.get("path")
            )


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
