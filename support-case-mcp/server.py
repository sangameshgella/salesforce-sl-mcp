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
            description="Get FRESH case details from Salesforce by Case Number. ALWAYS call this to get current data - do NOT rely on previous context or cached data. Use when user asks to 'check again', 'refresh', or 'what's the latest'. Returns Subject, Description, Status, and Comments.",
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
        # === AGENTIC TOOLS ===
        Tool(
            name="analyze_case",
            description="Get comprehensive case analysis with FRESH data from Salesforce. Includes case details, emails, comments, history, related articles, and AI-generated insights. ALWAYS fetches latest data - never uses cached information. Use this for a complete understanding of any case or when user wants current status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"},
                    "depth": {"type": "string", "description": "Analysis depth: 'quick' for basic info, 'full' for complete data including emails", "enum": ["quick", "full"], "default": "full"}
                },
                "required": ["case_number"],
            },
        ),
        Tool(
            name="follow_up_case",
            description="Handle customer follow-up on existing case. Analyzes context, determines what's been done, and generates appropriate response with suggested next steps. Perfect for 'what's the status?' or 'what has been done?' questions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"},
                    "customer_question": {"type": "string", "description": "What the customer is asking about"}
                },
                "required": ["case_number", "customer_question"],
            },
        ),
        Tool(
            name="triage_new_case",
            description="Triage a new or unassigned case. Classifies priority, finds similar past cases, suggests knowledge articles, and recommends actions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number to triage"},
                    "additional_context": {"type": "string", "description": "Any additional context about the case"}
                },
                "required": ["case_number"],
            },
        ),
        Tool(
            name="handle_request",
            description="Intelligent request handler. Describe what you need in natural language and this will route to the appropriate tools and return a consolidated response. Examples: 'status of case X', 'customer asking about X', 'find cases about Y', 'ready to close X?'",
            inputSchema={
                "type": "object",
                "properties": {
                    "request": {"type": "string", "description": "Natural language request"},
                    "context": {"type": "object", "description": "Optional context like case_number if known", "properties": {"case_number": {"type": "string"}}}
                },
                "required": ["request"],
            },
        ),
        # ========== SCHEMA TOOLS (Call before any write operation) ==========
        Tool(
            name="describe_sobject",
            description="Get field metadata for a Salesforce object. MUST call this BEFORE any update operation to know valid field names, data types, and picklist values. Returns field API names, labels, types, and valid picklist options with both display labels and API values. Use this to map user-friendly terms (like 'closed') to exact API values (like 'Closed').",
            inputSchema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "Salesforce object API name (e.g., 'Case', 'CaseComment', 'EmailMessage')"}
                },
                "required": ["object_name"],
            },
        ),
        Tool(
            name="describe_workflow_objects",
            description="Get field metadata for ALL objects in the support case workflow (Case, CaseComment, EmailMessage, Knowledge). Call this at the start of a session or before complex multi-object operations to understand all available fields and valid values across the workflow.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        # ========== EMAIL TOOLS ==========
        Tool(
            name="get_case_emails",
            description="Fetch all email messages linked to a case. Returns the complete email thread with sender, recipient, subject, body, and date. Use this to understand customer communication history before drafting a response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"}
                },
                "required": ["case_number"],
            },
        ),
        Tool(
            name="draft_case_email",
            description="Create a draft email preview for user approval. Does NOT send the email. Use this BEFORE send_case_email to show the user what will be sent. Returns draft with recipient, subject, and body for review.",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number to respond to"},
                    "message": {"type": "string", "description": "The email body content"}
                },
                "required": ["case_number", "message"],
            },
        ),
        Tool(
            name="send_case_email",
            description="Send an email to the case contact via Apex Email Services. ONLY call this AFTER user approves a draft. The email is ACTUALLY SENT (not just logged) and recorded in case activity. Returns success status AND contextual next_actions suggesting what to do next (e.g., create KBA, close case).",
            inputSchema={
                "type": "object",
                "properties": {
                    "case_number": {"type": "string", "description": "The Case Number"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body content"}
                },
                "required": ["case_number", "subject", "body"],
            },
        ),
        # ========== CASE WRITE TOOLS ==========
        Tool(
            name="update_case",
            description="Update case fields. MUST call describe_sobject('Case') first to get valid field names and picklist values. Returns success status AND contextual next_actions - after closing a case, suggests sending closure email and creating KBA. Always present these next actions to the user.",
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
            description="Add a comment to a case. Use for internal notes (is_public=false) or customer-visible responses (is_public=true). Returns success status AND contextual next_actions suggesting follow-up steps like sending email or updating status.",
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

@server.call_tool()
async def call_tool(name, arguments):
    logger.info("call_tool invoked: %s", name)
    if name == "search":
        query = arguments.get("query")
        results = sf_client.search_cases(query)
        if not results:
            return [{"type": "text", "text": "No cases found matching that query.\n\n💡 SUGGESTED: Try different keywords or use 'search_cases' for broader results."}]

        output = []
        for r in results:
            output.append(f"{r['CaseNumber']}: {r['Subject']} ({r['Status']})")
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append("  • fetch: Get full details for any case above")
        output.append("  • analyze_case: Get comprehensive analysis with insights")

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
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append(f"  • get_case_history: See what changes have been made")
        output.append(f"  • get_related_cases: Find similar cases")
        output.append(f"  • analyze_case: Get comprehensive analysis with AI insights")

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
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append(f"  • get_case_history: See field change history")
        output.append(f"  • get_case_timeline: See activity feed")
        output.append(f"  • get_case_summary: Get closure readiness assessment")
            
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "search_cases":
        query = arguments.get("query_string")
        results = sf_client.search_cases(query)
        if not results:
            return [{"type": "text", "text": "No cases found matching that query.\n\n💡 SUGGESTED: Try broader keywords or check spelling."}]
        
        output = [f"Found {len(results)} cases:"]
        for r in results:
            desc_snippet = (r.get('Description') or "")[:100].replace('\n', ' ')
            if len(r.get('Description') or "") > 100:
                desc_snippet += "..."
            output.append(f"- [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
            if desc_snippet:
                 output.append(f"  Snippet: {desc_snippet}")
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append("  • get_case_details: Get full details for a specific case")
        output.append("  • analyze_case: Get comprehensive analysis for any case")
        output.append("  • get_related_cases: Find cases related to a specific case")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_case_history":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        history = sf_client.get_case_history(case['Id'])
        if not history:
            return [{"type": "text", "text": f"No history found for case {case_number}.\n\n💡 SUGGESTED: Check get_case_timeline for activity feed."}]
        
        output = [f"Case {case_number} - Field Change History:", ""]
        for h in history:
            field = h.get('Field', 'Unknown')
            old_val = h.get('OldValue', '(empty)')
            new_val = h.get('NewValue', '(empty)')
            date = h.get('CreatedDate', '')
            user = h.get('CreatedBy', {}).get('Name', 'Unknown') if h.get('CreatedBy') else 'Unknown'
            output.append(f"[{date}] {user}: {field} changed from '{old_val}' to '{new_val}'")
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append(f"  • get_case_timeline: See posts and activities")
        output.append(f"  • get_case_summary: Get closure readiness assessment")
        output.append(f"  • follow_up_case: Generate customer follow-up response")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "get_case_timeline":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        feed = sf_client.get_case_feed(case['Id'])
        if not feed:
            return [{"type": "text", "text": f"No timeline/feed found for case {case_number}.\n\n💡 SUGGESTED: Check get_case_history for field changes."}]
        
        output = [f"Case {case_number} - Activity Timeline:", ""]
        for f in feed:
            body = f.get('Body', '(no content)')
            feed_type = f.get('Type', 'Post')
            date = f.get('CreatedDate', '')
            user = f.get('CreatedBy', {}).get('Name', 'Unknown') if f.get('CreatedBy') else 'Unknown'
            output.append(f"[{date}] [{feed_type}] {user}: {body}")
        
        output.append("")
        output.append("💡 SUGGESTED NEXT ACTIONS:")
        output.append(f"  • get_case_summary: Get closure readiness assessment")
        output.append(f"  • follow_up_case: Generate customer follow-up response")
        output.append(f"  • analyze_case: Get comprehensive case analysis")
        
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

    # === AGENTIC TOOL HANDLERS ===
    
    elif name == "analyze_case":
        case_number = arguments.get("case_number")
        depth = arguments.get("depth", "full")
        
        data = sf_client.get_comprehensive_case_data(case_number, depth)
        if not data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_info = data['case_info']
        tech = data['technical_summary']
        metrics = data.get('metrics', {})
        risk_factors = data.get('risk_factors', [])
        
        # Build comprehensive output
        output = [
            f"{'='*50}",
            f"CASE ANALYSIS: {case_info['CaseNumber']}",
            f"{'='*50}",
            "",
            "📋 CASE SUMMARY",
            f"  Subject: {case_info['Subject']}",
            f"  Status: {case_info['Status']}",
            f"  Priority: {case_info['Priority']}",
            f"  Created: {case_info['CreatedDate']}",
            f"  Last Updated: {case_info['LastModifiedDate']}",
            f"  Contact: {case_info['ContactName'] or 'N/A'}",
            "",
            "🔧 TECHNICAL STATUS",
            f"  Fix Status: {tech['fix_status'] or 'Not set'}",
            f"  Validation Status: {tech['validation_status'] or 'Not set'}",
            f"  Closure Readiness: {tech['closure_readiness']}",
            "",
        ]
        
        # Add metrics
        if metrics:
            output.extend([
                "📊 METRICS",
                f"  Days Since Update: {metrics.get('days_since_update', 'N/A')}",
                f"  Total Comments: {metrics.get('total_comments', 0)}",
                f"  Related Cases: {metrics.get('related_cases_count', 0)}",
                f"  Knowledge Articles: {metrics.get('articles_count', 0)}",
                "",
            ])
        
        # Add risk factors
        if risk_factors:
            output.extend([
                "⚠️ RISK FACTORS",
            ])
            for rf in risk_factors:
                output.append(f"  • {rf}")
            output.append("")
        
        # Add description
        output.extend([
            "📝 DESCRIPTION",
            case_info['Description'] or '(No description)',
            "",
        ])
        
        # Full mode: add history and comments
        if depth == "full":
            if data.get('history'):
                output.extend(["📜 RECENT CHANGES"])
                for h in data['history'][:5]:
                    field = h.get('Field', 'Unknown')
                    new_val = h.get('NewValue', '')
                    date = h.get('CreatedDate', '')[:10] if h.get('CreatedDate') else ''
                    output.append(f"  [{date}] {field} → {new_val}")
                output.append("")
            
            if data.get('recent_comments'):
                output.extend(["💬 RECENT COMMENTS"])
                for c in data['recent_comments'][:3]:
                    user = c.get('CreatedBy', {}).get('Name', 'Unknown') if c.get('CreatedBy') else 'Unknown'
                    body = (c.get('CommentBody', '')[:100] + '...') if len(c.get('CommentBody', '')) > 100 else c.get('CommentBody', '')
                    output.append(f"  • {user}: {body}")
                output.append("")
            
            if data.get('related_cases'):
                output.extend(["🔗 RELATED CASES"])
                for r in data['related_cases'][:3]:
                    output.append(f"  • [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
                output.append("")
        
        # Generate suggested actions based on case state
        suggested_actions = []
        if tech['closure_readiness'] == 'ready':
            suggested_actions.append({"action": "close_case", "reason": "Fix implemented and validated", "priority": "high"})
            suggested_actions.append({"action": "create_kba", "reason": "Convert to knowledge article", "priority": "medium"})
        elif tech['closure_readiness'] == 'pending_validation':
            suggested_actions.append({"action": "run_validation", "reason": "Complete validation testing", "priority": "high"})
            suggested_actions.append({"action": "contact_customer", "reason": "Confirm fix works", "priority": "medium"})
        else:
            suggested_actions.append({"action": "investigate", "reason": "Case needs attention", "priority": "high"})
        
        if metrics.get('days_since_update', 0) and metrics['days_since_update'] > 7:
            suggested_actions.append({"action": "follow_up", "reason": f"No activity for {metrics['days_since_update']} days", "priority": "high"})
        
        if metrics.get('related_cases_count', 0) > 2:
            suggested_actions.append({"action": "check_patterns", "reason": "Multiple similar cases exist", "priority": "medium"})
        
        output.extend([
            "💡 SUGGESTED ACTIONS",
        ])
        for sa in suggested_actions:
            output.append(f"  [{sa['priority'].upper()}] {sa['action']}: {sa['reason']}")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "follow_up_case":
        case_number = arguments.get("case_number")
        customer_question = arguments.get("customer_question", "")
        
        data = sf_client.get_comprehensive_case_data(case_number, "full")
        if not data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_info = data['case_info']
        tech = data['technical_summary']
        history = data.get('history', [])
        comments = data.get('recent_comments', [])
        
        output = [
            f"{'='*50}",
            f"FOLLOW-UP RESPONSE: {case_info['CaseNumber']}",
            f"{'='*50}",
            "",
            f"Customer Question: {customer_question}",
            "",
            "📋 CURRENT STATUS",
            f"  Case Status: {case_info['Status']}",
            f"  Fix Status: {tech['fix_status'] or 'In Progress'}",
            f"  Validation: {tech['validation_status'] or 'Pending'}",
            "",
        ]
        
        # What has been done
        output.append("✅ WHAT HAS BEEN DONE")
        if tech['fix_status'] == 'Implemented':
            output.append("  • A fix has been implemented by the engineering team")
        if tech['validation_status'] == 'Completed':
            output.append("  • Validation/testing has been completed successfully")
        elif tech['validation_status']:
            output.append(f"  • Validation is currently: {tech['validation_status']}")
        
        if history:
            output.append("  • Recent changes:")
            for h in history[:3]:
                field = h.get('Field', 'Unknown')
                new_val = h.get('NewValue', '')
                date = h.get('CreatedDate', '')[:10] if h.get('CreatedDate') else ''
                output.append(f"    - [{date}] {field} updated to '{new_val}'")
        output.append("")
        
        # Generate response based on closure readiness
        output.append("📝 SUGGESTED RESPONSE TO CUSTOMER")
        output.append("-" * 40)
        
        if tech['closure_readiness'] == 'ready':
            output.extend([
                "The issue has been resolved. Our team has:",
                "  1. Implemented the necessary fix",
                "  2. Completed validation testing",
                "",
                "The case is ready for closure. Please confirm if the issue",
                "is resolved on your end, and we can close this case.",
            ])
        elif tech['closure_readiness'] == 'pending_validation':
            output.extend([
                "We have implemented a fix for this issue. Our team is",
                "currently completing the validation/testing phase.",
                "",
                "Once validation is complete, we will notify you and",
                "coordinate to confirm the fix resolves your issue.",
            ])
        else:
            output.extend([
                f"Your case is currently being actively worked on.",
                f"Current status: {case_info['Status']}",
                "",
                "Our team is investigating the issue. We will provide",
                "updates as we make progress.",
            ])
        output.append("-" * 40)
        output.append("")
        
        # Suggested next actions
        output.append("💡 SUGGESTED NEXT ACTIONS")
        if tech['closure_readiness'] == 'ready':
            output.append("  [HIGH] Contact customer to confirm resolution")
            output.append("  [MEDIUM] Prepare case for closure")
            output.append("  [LOW] Consider creating Knowledge Article")
        elif tech['closure_readiness'] == 'pending_validation':
            output.append("  [HIGH] Complete validation testing")
            output.append("  [MEDIUM] Update customer on timeline")
        else:
            output.append("  [HIGH] Continue investigation")
            output.append("  [MEDIUM] Provide status update to customer")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "triage_new_case":
        case_number = arguments.get("case_number")
        additional_context = arguments.get("additional_context", "")
        
        data = sf_client.get_comprehensive_case_data(case_number, "full")
        if not data:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        case_info = data['case_info']
        related = data.get('related_cases', [])
        articles = data.get('knowledge_articles', [])
        
        output = [
            f"{'='*50}",
            f"CASE TRIAGE: {case_info['CaseNumber']}",
            f"{'='*50}",
            "",
            "📋 CASE DETAILS",
            f"  Subject: {case_info['Subject']}",
            f"  Current Priority: {case_info['Priority']}",
            f"  Status: {case_info['Status']}",
            f"  Contact: {case_info['ContactName'] or 'N/A'}",
            "",
            "📝 DESCRIPTION",
            case_info['Description'] or '(No description)',
            "",
        ]
        
        # Priority recommendation based on keywords and context
        desc_lower = (case_info['Description'] or '').lower()
        subject_lower = (case_info['Subject'] or '').lower()
        
        priority_indicators = {
            'critical': ['critical', 'urgent', 'down', 'outage', 'production', 'blocker'],
            'high': ['high', 'important', 'asap', 'deadline', 'customer escalation'],
            'medium': ['issue', 'problem', 'bug', 'error'],
            'low': ['question', 'inquiry', 'enhancement', 'feature request']
        }
        
        recommended_priority = case_info['Priority']  # default to current
        priority_reason = "Based on current assignment"
        
        for priority, keywords in priority_indicators.items():
            if any(kw in desc_lower or kw in subject_lower for kw in keywords):
                recommended_priority = priority.capitalize()
                matched = [kw for kw in keywords if kw in desc_lower or kw in subject_lower]
                priority_reason = f"Keywords detected: {', '.join(matched[:2])}"
                break
        
        output.extend([
            "🎯 PRIORITY ASSESSMENT",
            f"  Current: {case_info['Priority']}",
            f"  Recommended: {recommended_priority}",
            f"  Reason: {priority_reason}",
            "",
        ])
        
        # Similar cases
        if related:
            output.extend([
                "🔗 SIMILAR PAST CASES",
                "  (Review these for potential solutions)",
            ])
            for r in related[:5]:
                output.append(f"  • [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
            output.append("")
        else:
            output.append("🔗 No similar cases found in history")
            output.append("")
        
        # Knowledge articles
        if articles:
            output.extend([
                "📚 RELEVANT KNOWLEDGE ARTICLES",
            ])
            for a in articles:
                ka = a.get('KnowledgeArticle', {})
                output.append(f"  • {ka.get('Title', 'Untitled')}")
            output.append("")
        
        # Triage recommendations
        output.extend([
            "💡 TRIAGE RECOMMENDATIONS",
            f"  1. Set priority to: {recommended_priority}",
        ])
        
        if related:
            output.append(f"  2. Review {len(related)} similar cases for patterns/solutions")
        if articles:
            output.append(f"  3. Check {len(articles)} knowledge articles before investigation")
        
        # Suggest assignment based on case type
        if 'firmware' in desc_lower or 'sdk' in desc_lower:
            output.append("  4. Assign to: Firmware/SDK Team")
        elif 'hardware' in desc_lower or 'board' in desc_lower:
            output.append("  4. Assign to: Hardware Team")
        elif 'certification' in desc_lower or 'compliance' in desc_lower:
            output.append("  4. Assign to: Compliance Team")
        else:
            output.append("  4. Assign to: General Support Queue")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "handle_request":
        request = arguments.get("request", "").lower()
        context = arguments.get("context", {})
        case_number = context.get("case_number") if context else None
        
        # Extract case number from request if not in context
        import re
        if not case_number:
            # Look for patterns like "case 00335943" or just "00335943"
            match = re.search(r'(?:case\s+)?(\d{8})', request)
            if match:
                case_number = match.group(1)
        
        output = [
            f"{'='*50}",
            "INTELLIGENT REQUEST HANDLER",
            f"{'='*50}",
            "",
            f"Request: {arguments.get('request', '')}",
            f"Detected Case: {case_number or 'None'}",
            "",
        ]
        
        # Intent classification
        intent = None
        
        if any(word in request for word in ['status', 'update', 'progress', "what's happening"]):
            intent = 'status_check'
        elif any(word in request for word in ['customer asking', 'follow up', 'follow-up', 'what has been done', "what's been done"]):
            intent = 'follow_up'
        elif any(word in request for word in ['find', 'search', 'look for', 'cases about']):
            intent = 'search'
        elif any(word in request for word in ['ready to close', 'can we close', 'closure']):
            intent = 'closure_check'
        elif any(word in request for word in ['triage', 'new case', 'prioritize']):
            intent = 'triage'
        elif any(word in request for word in ['kba', 'knowledge article', 'document']):
            intent = 'kba_check'
        elif any(word in request for word in ['analyze', 'analysis', 'details', 'tell me about']):
            intent = 'analyze'
        else:
            intent = 'general'
        
        output.extend([
            f"🎯 Detected Intent: {intent}",
            "",
        ])
        
        # Route to appropriate handler
        if intent == 'status_check' or intent == 'analyze':
            if case_number:
                output.append("→ Routing to: analyze_case")
                output.append("")
                # Call analyze_case internally
                result = await call_tool("analyze_case", {"case_number": case_number, "depth": "full"})
                return result
            else:
                output.append("⚠️ Please specify a case number for status check.")
        
        elif intent == 'follow_up':
            if case_number:
                output.append("→ Routing to: follow_up_case")
                output.append("")
                result = await call_tool("follow_up_case", {"case_number": case_number, "customer_question": request})
                return result
            else:
                output.append("⚠️ Please specify a case number for follow-up.")
        
        elif intent == 'search':
            # Extract search terms
            search_terms = request.replace('find', '').replace('search', '').replace('cases about', '').replace('look for', '').strip()
            output.append(f"→ Routing to: search_cases with query: '{search_terms}'")
            output.append("")
            result = await call_tool("search_cases", {"query_string": search_terms})
            return result
        
        elif intent == 'closure_check':
            if case_number:
                output.append("→ Routing to: get_case_summary (closure check)")
                output.append("")
                result = await call_tool("get_case_summary", {"case_number": case_number})
                return result
            else:
                output.append("⚠️ Please specify a case number for closure check.")
        
        elif intent == 'triage':
            if case_number:
                output.append("→ Routing to: triage_new_case")
                output.append("")
                result = await call_tool("triage_new_case", {"case_number": case_number})
                return result
            else:
                output.append("⚠️ Please specify a case number for triage.")
        
        elif intent == 'kba_check':
            if case_number:
                output.append("→ Routing to: suggest_knowledge_article")
                output.append("")
                result = await call_tool("suggest_knowledge_article", {"case_number": case_number})
                return result
            else:
                output.append("⚠️ Please specify a case number for KBA check.")
        
        else:
            output.extend([
                "🤔 I couldn't determine a specific intent.",
                "",
                "Available actions:",
                "  • 'status of case XXXXXXXX' - Get case status",
                "  • 'customer asking about case XXXXXXXX' - Generate follow-up response",
                "  • 'find cases about [topic]' - Search for cases",
                "  • 'ready to close case XXXXXXXX?' - Check closure readiness",
                "  • 'triage case XXXXXXXX' - Triage a new case",
                "  • 'create KBA for case XXXXXXXX' - Check KBA eligibility",
            ])
        
        return [{"type": "text", "text": "\n".join(output)}]

    # ========== SCHEMA TOOL HANDLERS ==========
    
    elif name == "describe_sobject":
        object_name = arguments.get("object_name")
        if not object_name:
            return [{"type": "text", "text": "Error: object_name is required."}]
        
        result = sf_client.describe_sobject(object_name)
        
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
        result = sf_client.describe_workflow_objects()
        
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

    # ========== EMAIL TOOL HANDLERS ==========
    
    elif name == "get_case_emails":
        case_number = arguments.get("case_number")
        case = sf_client.get_case(case_number)
        if not case:
            return [{"type": "text", "text": f"Case {case_number} not found."}]
        
        emails = sf_client.get_case_emails(case['Id'])
        if not emails:
            return [{"type": "text", "text": f"No emails found for case {case_number}."}]
        
        output = [f"=== Email Thread for Case {case_number} ===", f"Total: {len(emails)} emails", ""]
        
        for i, email in enumerate(emails, 1):
            direction = email['direction'].upper()
            output.append(f"[{i}] [{direction}] {email['date']}")
            output.append(f"    From: {email['from']}")
            output.append(f"    To: {email['to']}")
            output.append(f"    Subject: {email['subject']}")
            body_preview = (email['body'] or '')[:300].replace('\n', ' ')
            if len(email['body'] or '') > 300:
                body_preview += "..."
            output.append(f"    Body: {body_preview}")
            output.append("")
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "draft_case_email":
        case_number = arguments.get("case_number")
        message = arguments.get("message")
        
        if not message:
            return [{"type": "text", "text": "Error: message is required."}]
        
        result = sf_client.draft_case_email(case_number, message)
        
        if not result['success']:
            return [{"type": "text", "text": f"Error: {result['error']}"}]
        
        output = [
            "=== DRAFT EMAIL (Review Before Sending) ===",
            "",
            f"To: {result['to_name']} <{result['to_email']}>",
            f"Subject: {result['subject']}",
            "",
            "--- Body ---",
            result['body'],
            "--- End ---",
            "",
            "⚠️ This is a DRAFT. Call send_case_email to send this email.",
            "   Or modify the message and call draft_case_email again."
        ]
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "send_case_email":
        case_number = arguments.get("case_number")
        subject = arguments.get("subject")
        body = arguments.get("body")
        
        if not all([subject, body]):
            return [{"type": "text", "text": "Error: subject and body are required."}]
        
        result = sf_client.send_case_email(case_number, subject, body)
        
        if not result['success']:
            return [{"type": "text", "text": f"Error sending email: {result['error']}"}]
        
        output = [
            "✅ EMAIL SENT SUCCESSFULLY",
            "",
            f"Case: {result['case_number']}",
            f"Sent to: {result['sent_to']}",
            f"Subject: {result['subject']}",
            "",
            "The email has been logged to the case automatically.",
            "",
            "💡 SUGGESTED: Add a case comment to log this action."
        ]
        
        return [{"type": "text", "text": "\n".join(output)}]

    # ========== CASE WRITE TOOL HANDLERS ==========
    
    elif name == "update_case":
        case_number = arguments.get("case_number")
        fields = arguments.get("fields")
        
        if not fields or not isinstance(fields, dict):
            return [{"type": "text", "text": "Error: fields must be a dictionary of field names and values."}]
        
        result = sf_client.update_case(case_number, fields)
        
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
        
        return [{"type": "text", "text": "\n".join(output)}]

    elif name == "add_case_comment":
        case_number = arguments.get("case_number")
        comment = arguments.get("comment")
        is_public = arguments.get("is_public", False)
        
        if not comment:
            return [{"type": "text", "text": "Error: comment is required."}]
        
        result = sf_client.add_case_comment(case_number, comment, is_public)
        
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
        
        result = sf_client.create_knowledge_article(title, summary, content, case_number=case_number)
        
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
