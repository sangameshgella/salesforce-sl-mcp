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

    def get_case_history(self, case_id: str) -> List[Dict[str, Any]]:
        """Fetch case field change history to show what changes were made"""
        self.connect()
        try:
            query = f"""
                SELECT Field, OldValue, NewValue, CreatedDate, CreatedBy.Name 
                FROM CaseHistory 
                WHERE CaseId = '{case_id}' 
                ORDER BY CreatedDate DESC 
                LIMIT 20
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error fetching history for {case_id}: {e}")
            return []

    def get_case_feed(self, case_id: str) -> List[Dict[str, Any]]:
        """Fetch case feed items (posts, activities, updates)"""
        self.connect()
        try:
            query = f"""
                SELECT Body, Type, CreatedDate, CreatedBy.Name 
                FROM CaseFeed 
                WHERE ParentId = '{case_id}' 
                ORDER BY CreatedDate DESC
                LIMIT 20
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error fetching feed for {case_id}: {e}")
            return []

    def get_case_with_status(self, case_number: str) -> Optional[Dict[str, Any]]:
        """Get case with custom status fields for fix/validation tracking"""
        self.connect()
        try:
            query = f"""
                SELECT Id, CaseNumber, Subject, Description, Status, Priority, 
                       Contact.Name, CreatedDate, LastModifiedDate,
                       Fix_Status__c, Validation_Status__c
                FROM Case 
                WHERE CaseNumber = '{case_number}' 
                LIMIT 1
            """
            result = self.sf.query(query)
            if result['totalSize'] > 0:
                return result['records'][0]
            return None
        except Exception as e:
            print(f"Error fetching case with status {case_number}: {e}")
            return None

    def get_case_summary_data(self, case_number: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive case data for AI summarization"""
        case = self.get_case_with_status(case_number)
        if not case:
            return None
        
        case_id = case['Id']
        history = self.get_case_history(case_id)
        comments = self.get_case_comments(case_id)
        feed = self.get_case_feed(case_id)
        
        # Determine closure readiness based on status fields
        fix_status = case.get('Fix_Status__c', '')
        validation_status = case.get('Validation_Status__c', '')
        
        if fix_status == 'Implemented' and validation_status == 'Completed':
            closure_readiness = 'ready'
        elif fix_status == 'Implemented':
            closure_readiness = 'pending_validation'
        else:
            closure_readiness = 'in_progress'
        
        return {
            'case_info': {
                'CaseNumber': case['CaseNumber'],
                'Subject': case['Subject'],
                'Description': case['Description'],
                'Status': case['Status'],
                'Priority': case['Priority'],
                'CreatedDate': case.get('CreatedDate'),
                'LastModifiedDate': case.get('LastModifiedDate'),
                'ContactName': case.get('Contact', {}).get('Name') if case.get('Contact') else None
            },
            'technical_summary': {
                'fix_status': fix_status,
                'validation_status': validation_status,
                'closure_readiness': closure_readiness
            },
            'history': history[:10],  # Last 10 changes
            'recent_comments': comments[:5],  # Last 5 comments
            'feed_items': feed[:10]  # Last 10 feed items
        }

    def get_related_cases(self, case_id: str, subject: str) -> List[Dict[str, Any]]:
        """Find cases with similar subject or linked to the same contact/account"""
        self.connect()
        try:
            # Extract first few keywords from subject for fuzzy match
            words = [w for w in subject.split() if len(w) > 3][:3]
            if not words:
                return []
            
            # Build LIKE conditions for each keyword
            like_conditions = " OR ".join([f"Subject LIKE '%{self._escape_soql(w)}%'" for w in words])
            
            query = f"""
                SELECT Id, CaseNumber, Subject, Status, CreatedDate
                FROM Case
                WHERE Id != '{case_id}'
                AND ({like_conditions})
                ORDER BY CreatedDate DESC
                LIMIT 10
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error fetching related cases: {e}")
            return []

    def _escape_soql(self, text: str) -> str:
        """Escape single quotes for SOQL queries"""
        if not text:
            return text
        return text.replace("'", "\\'").replace("\\", "\\\\")

    def get_case_articles(self, case_id: str) -> List[Dict[str, Any]]:
        """Get knowledge articles linked to a case"""
        self.connect()
        try:
            # Query CaseArticle junction object
            query = f"""
                SELECT KnowledgeArticleId, 
                       KnowledgeArticle.Title,
                       KnowledgeArticle.UrlName
                FROM CaseArticle
                WHERE CaseId = '{case_id}'
                LIMIT 10
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error fetching articles for case {case_id}: {e}")
            return []
