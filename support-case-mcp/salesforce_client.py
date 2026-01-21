import os
from typing import Dict, Any, List, Optional
from simple_salesforce import Salesforce
from dotenv import load_dotenv

load_dotenv()

class SalesforceClient:
    def __init__(self):
        self.username = os.getenv("SF_USERNAME")
        self.password = os.getenv("SF_PASSWORD")
        self.token = os.getenv("SF_SECURITY_TOKEN")
        self.domain = os.getenv("SF_DOMAIN", "login")
        
        self.sf = None
        
    def connect(self):
        if not self.sf:
            if not all([self.username, self.password, self.token]):
                raise ValueError("Missing Salesforce credentials in .env")
            
            self.sf = Salesforce(
                username=self.username,
                password=self.password,
                security_token=self.token,
                domain=self.domain
            )
            
    def get_case(self, case_number: str) -> Optional[Dict[str, Any]]:
        self.connect()
        try:
            # Query for the Case by CaseNumber
            query = f"SELECT Id, CaseNumber, Subject, Description, Status, Priority, Contact.Name FROM Case WHERE CaseNumber = '{case_number}' LIMIT 1"
            result = self.sf.query(query)
            
            if result['totalSize'] > 0:
                record = result['records'][0]
                # Fetch recent comments if needed, or structured differently
                return record
            return None
        except Exception as e:
            print(f"Error fetching case {case_number}: {e}")
            return None

    def _escape_sosl(self, text: str) -> str:
        """
        Escapes reserved characters in SOSL search queries.
        Reserved: ? & | ! { } [ ] ( ) ^ ~ * : \ " ' + -
        """
        if not text:
            return text
            
        # List of reserved characters to escape
        reserved_chars = [
            '\\', '?', '&', '|', '!', '{', '}', '[', ']', '(', ')', 
            '^', '~', '*', ':', '"', "'", '+', '-'
        ]
        
        escaped = ""
        for char in text:
            if char in reserved_chars:
                escaped += f"\\{char}"
            else:
                escaped += char
        return escaped

    def search_cases(self, query_text: str) -> List[Dict[str, Any]]:
        self.connect()
        try:
            # Sanitize user input
            escaped_query = self._escape_sosl(query_text)
            
            # Use braces match with escaped content
            sosl = f"FIND {{{escaped_query}}} IN ALL FIELDS RETURNING Case(Id, CaseNumber, Subject, Status, Description)"
            
            with open("debug.log", "a") as f:
                f.write(f"DEBUG SOSL: {sosl}\n")

            result = self.sf.search(sosl)
            return result.get('searchRecords', [])
        except Exception as e:
            print(f"Error searching cases: {e}")
            return []

    def get_case_comments(self, case_id: str) -> List[Dict[str, Any]]:
        self.connect()
        try:
            query = f"SELECT CommentBody, CreatedDate, CreatedBy.Name FROM CaseComment WHERE ParentId = '{case_id}' ORDER BY CreatedDate DESC"
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
             print(f"Error fetching comments for {case_id}: {e}")
             return []
