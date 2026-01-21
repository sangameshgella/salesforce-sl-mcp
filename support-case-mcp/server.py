from mcp.server.fastmcp import FastMCP
from support_case_mcp.salesforce_client import SalesforceClient

# Initialize FastMCP Server
mcp = FastMCP("Support Case MCP")
sf_client = SalesforceClient()

@mcp.tool()
def get_case_details(case_number: str) -> str:
    """
    Get full details of a support case by its Case Number (e.g., 00335943).
    Returns the Subject, Description, Status, and recent Comments.
    """
    case = sf_client.get_case(case_number)
    if not case:
        return f"Case {case_number} not found."
    
    # Enrich with comments
    comments = sf_client.get_case_comments(case['Id'])
    
    # Format output
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
        
    return "\n".join(output)

@mcp.tool()
def search_cases(query_string: str) -> str:
    """
    Search for support cases using a keyword or phrase. 
    Useful when you don't have the exact Case Number.
    Returns a list of matching cases with ID, Number, Subject and Status.
    """
    results = sf_client.search_cases(query_string)
    if not results:
        return "No cases found matching that query."
    
    output = [f"Found {len(results)} cases:"]
    for r in results:
        output.append(f"- [{r['CaseNumber']}] {r['Subject']} ({r['Status']})")
        
    return "\n".join(output)

if __name__ == "__main__":
    mcp.run()
