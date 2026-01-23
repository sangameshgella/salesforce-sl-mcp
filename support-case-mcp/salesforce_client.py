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
        """
        Search for cases using a combined strategy:
        1. Try fast SOSL full-text search first
        2. If no results, fall back to fuzzy SOQL LIKE search
        """
        self.connect()
        
        # Try SOSL first (fast, indexed full-text search)
        results = self._search_cases_sosl(query_text)
        if results:
            return results
        
        # Fall back to fuzzy SOQL search (slower but more flexible)
        return self._search_cases_fuzzy(query_text)

    def _search_cases_sosl(self, query_text: str) -> List[Dict[str, Any]]:
        """Fast SOSL full-text search (exact word matching)"""
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
            print(f"Error in SOSL search: {e}")
            return []

    def _search_cases_fuzzy(self, query_text: str) -> List[Dict[str, Any]]:
        """
        Fuzzy search using SOQL LIKE for partial matching.
        Searches for each term in Subject and Description fields.
        """
        try:
            # Split query into individual terms, filter out very short words
            terms = [t.strip() for t in query_text.split() if len(t.strip()) >= 2]
            
            if not terms:
                return []
            
            # Build LIKE conditions for Subject and Description
            like_conditions = []
            for term in terms:
                escaped = self._escape_soql(term)
                like_conditions.append(f"Subject LIKE '%{escaped}%'")
                like_conditions.append(f"Description LIKE '%{escaped}%'")
            
            where_clause = " OR ".join(like_conditions)
            
            query = f"""
                SELECT Id, CaseNumber, Subject, Status, Description 
                FROM Case 
                WHERE {where_clause}
                ORDER BY LastModifiedDate DESC 
                LIMIT 50
            """
            
            with open("debug.log", "a") as f:
                f.write(f"DEBUG SOQL Fuzzy: {query}\n")
            
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error in fuzzy SOQL search: {e}")
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

    def get_comprehensive_case_data(self, case_number: str, depth: str = "full") -> Optional[Dict[str, Any]]:
        """
        Get all case data in a single composite call for agentic workflows.
        
        Args:
            case_number: The case number to fetch
            depth: "quick" for basic info, "full" for complete data
            
        Returns:
            Comprehensive case data with all related information
        """
        case = self.get_case_with_status(case_number)
        if not case:
            return None
        
        case_id = case['Id']
        
        # Determine closure readiness
        fix_status = case.get('Fix_Status__c', '') or ''
        validation_status = case.get('Validation_Status__c', '') or ''
        
        if fix_status == 'Implemented' and validation_status == 'Completed':
            closure_readiness = 'ready'
        elif fix_status == 'Implemented':
            closure_readiness = 'pending_validation'
        else:
            closure_readiness = 'in_progress'
        
        # Calculate days since last update
        from datetime import datetime
        last_modified = case.get('LastModifiedDate', '')
        days_since_update = None
        if last_modified:
            try:
                lm_date = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                days_since_update = (datetime.now(lm_date.tzinfo) - lm_date).days
            except:
                pass
        
        result = {
            'case_info': {
                'CaseNumber': case['CaseNumber'],
                'Subject': case['Subject'],
                'Description': case['Description'],
                'Status': case['Status'],
                'Priority': case['Priority'],
                'CreatedDate': case.get('CreatedDate'),
                'LastModifiedDate': case.get('LastModifiedDate'),
                'ContactName': case.get('Contact', {}).get('Name') if case.get('Contact') else None,
                'DaysSinceUpdate': days_since_update
            },
            'technical_summary': {
                'fix_status': fix_status,
                'validation_status': validation_status,
                'closure_readiness': closure_readiness
            }
        }
        
        if depth == "quick":
            # Quick mode: just basic info + comments count
            comments = self.get_case_comments(case_id)
            result['metrics'] = {
                'total_comments': len(comments),
                'has_recent_activity': days_since_update is not None and days_since_update < 7
            }
            return result
        
        # Full mode: get everything
        history = self.get_case_history(case_id)
        comments = self.get_case_comments(case_id)
        feed = self.get_case_feed(case_id)
        related = self.get_related_cases(case_id, case['Subject'])
        articles = self.get_case_articles(case_id)
        
        result['history'] = history[:10]
        result['recent_comments'] = comments[:5]
        result['feed_items'] = feed[:10]
        result['related_cases'] = related[:5]
        result['knowledge_articles'] = articles
        
        # Calculate metrics for insights
        result['metrics'] = {
            'total_comments': len(comments),
            'total_history_changes': len(history),
            'related_cases_count': len(related),
            'articles_count': len(articles),
            'has_recent_activity': days_since_update is not None and days_since_update < 7,
            'days_since_update': days_since_update
        }
        
        # Risk factors
        risk_factors = []
        if days_since_update and days_since_update > 14:
            risk_factors.append("No activity for 14+ days")
        if len(comments) == 0:
            risk_factors.append("No comments on case")
        if case['Priority'] == 'High' and closure_readiness != 'ready':
            risk_factors.append("High priority case not yet resolved")
        if len(related) > 3:
            risk_factors.append("Multiple similar cases exist - possible systemic issue")
        
        result['risk_factors'] = risk_factors
        
        return result
