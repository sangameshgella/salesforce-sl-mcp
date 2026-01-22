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
        r"""
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

    # ========== AI Summary Methods ==========

    def get_case_emails(self, case_id: str) -> List[Dict[str, Any]]:
        """Fetch email messages linked to a case for AI summarization"""
        self.connect()
        try:
            query = f"""
                SELECT Id, Subject, TextBody, HtmlBody, FromAddress, ToAddress, 
                       MessageDate, Incoming, Status
                FROM EmailMessage 
                WHERE RelatedToId = '{case_id}' 
                ORDER BY MessageDate DESC
                LIMIT 50
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            print(f"Error fetching emails for case {case_id}: {e}")
            return []

    def get_case_for_ai_summary(self, case_number: str) -> Optional[Dict[str, Any]]:
        """
        Get case data optimized for AI summary generation.
        Returns case description + all email messages in a structured format.
        """
        case = self.get_case(case_number)
        if not case:
            return None
        
        case_id = case['Id']
        emails = self.get_case_emails(case_id)
        comments = self.get_case_comments(case_id)
        
        # Format emails for AI consumption
        formatted_emails = []
        for email in emails:
            formatted_emails.append({
                'date': email.get('MessageDate'),
                'from': email.get('FromAddress'),
                'to': email.get('ToAddress'),
                'subject': email.get('Subject'),
                'body': email.get('TextBody') or email.get('HtmlBody', ''),
                'direction': 'inbound' if email.get('Incoming') else 'outbound'
            })
        
        # Format comments for AI consumption
        formatted_comments = []
        for comment in comments:
            formatted_comments.append({
                'date': comment.get('CreatedDate'),
                'author': comment.get('CreatedBy', {}).get('Name') if comment.get('CreatedBy') else 'Unknown',
                'body': comment.get('CommentBody', '')
            })
        
        return {
            'case_number': case['CaseNumber'],
            'subject': case['Subject'],
            'description': case['Description'],
            'status': case['Status'],
            'priority': case['Priority'],
            'contact_name': case.get('Contact', {}).get('Name') if case.get('Contact') else None,
            'emails': formatted_emails,
            'comments': formatted_comments,
            'email_count': len(formatted_emails),
            'comment_count': len(formatted_comments)
        }

    def update_case_ai_summary(self, case_number: str, summary: str) -> Dict[str, Any]:
        """
        Update the Case Summary (AI) field with the generated summary.
        Assumes custom field: Case_Summary_AI__c (Long Text Area)
        """
        self.connect()
        try:
            # First get the case Id
            case = self.get_case(case_number)
            if not case:
                return {'success': False, 'error': f'Case {case_number} not found'}
            
            case_id = case['Id']
            
            # Update the Case_Summary_AI__c field
            self.sf.Case.update(case_id, {'Case_Summary_AI__c': summary})
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case_id,
                'message': f'AI Summary updated for case {case_number}'
            }
        except Exception as e:
            print(f"Error updating AI summary for case {case_number}: {e}")
            return {'success': False, 'error': str(e)}

    # ========== Knowledge Article Methods ==========

    def search_knowledge_articles(self, query_text: str) -> List[Dict[str, Any]]:
        """
        Search Knowledge Base Articles by keyword/phrase.
        Returns articles with title, number, summary, and URL.
        """
        self.connect()
        try:
            escaped_query = self._escape_sosl(query_text)
            
            # SOSL search on Knowledge articles (Knowledge__kav is the versioned article object)
            sosl = f"""
                FIND {{{escaped_query}}} IN ALL FIELDS 
                RETURNING Knowledge__kav(
                    Id, KnowledgeArticleId, ArticleNumber, Title, Summary, 
                    UrlName, PublishStatus, VersionNumber, LastModifiedDate
                    WHERE PublishStatus = 'Online'
                )
            """
            
            result = self.sf.search(sosl)
            articles = result.get('searchRecords', [])
            
            # Format for cleaner output
            formatted = []
            for article in articles:
                formatted.append({
                    'id': article.get('Id'),
                    'knowledge_article_id': article.get('KnowledgeArticleId'),
                    'article_number': article.get('ArticleNumber'),
                    'title': article.get('Title'),
                    'summary': article.get('Summary'),
                    'url_name': article.get('UrlName'),
                    'version': article.get('VersionNumber'),
                    'last_modified': article.get('LastModifiedDate')
                })
            
            return formatted
        except Exception as e:
            print(f"Error searching knowledge articles: {e}")
            return []

    def get_knowledge_article(self, article_number: str) -> Optional[Dict[str, Any]]:
        """
        Get full Knowledge Article details by ArticleNumber.
        Returns title, summary, and full content/details for AI summarization.
        """
        self.connect()
        try:
            # Query the Knowledge__kav object (versioned article)
            # Note: The actual field names may vary based on your Salesforce Knowledge setup
            query = f"""
                SELECT Id, KnowledgeArticleId, ArticleNumber, Title, Summary,
                       UrlName, PublishStatus, VersionNumber, 
                       CreatedDate, LastModifiedDate,
                       ArticleType
                FROM Knowledge__kav 
                WHERE ArticleNumber = '{self._escape_soql(article_number)}'
                AND PublishStatus = 'Online'
                ORDER BY VersionNumber DESC
                LIMIT 1
            """
            
            result = self.sf.query(query)
            
            if result['totalSize'] == 0:
                return None
            
            article = result['records'][0]
            article_id = article['Id']
            
            # Try to get the article body/content
            # Note: Knowledge article body fields vary by record type
            # Common fields: Details__c, Content__c, Solution__c, etc.
            body_content = None
            try:
                # Try to get additional content fields
                body_query = f"""
                    SELECT Id, Details__c
                    FROM Knowledge__kav 
                    WHERE Id = '{article_id}'
                """
                body_result = self.sf.query(body_query)
                if body_result['totalSize'] > 0:
                    body_content = body_result['records'][0].get('Details__c')
            except:
                # Details__c field may not exist, that's okay
                pass
            
            return {
                'id': article['Id'],
                'knowledge_article_id': article.get('KnowledgeArticleId'),
                'article_number': article['ArticleNumber'],
                'title': article['Title'],
                'summary': article.get('Summary'),
                'details': body_content,
                'url_name': article.get('UrlName'),
                'article_type': article.get('ArticleType'),
                'version': article.get('VersionNumber'),
                'publish_status': article.get('PublishStatus'),
                'created_date': article.get('CreatedDate'),
                'last_modified': article.get('LastModifiedDate')
            }
        except Exception as e:
            print(f"Error fetching knowledge article {article_number}: {e}")
            return None

    def update_kba_summary(self, article_number: str, summary: str) -> Dict[str, Any]:
        """
        Update the Summary field of a Knowledge Article.
        Note: Updating Knowledge articles requires proper permissions and
        may need to create a new draft version depending on Salesforce setup.
        """
        self.connect()
        try:
            # First get the article
            article = self.get_knowledge_article(article_number)
            if not article:
                return {'success': False, 'error': f'Article {article_number} not found'}
            
            article_id = article['id']
            
            # Update the Summary field
            # Note: This may require the article to be in Draft status
            # depending on Salesforce Knowledge configuration
            self.sf.Knowledge__kav.update(article_id, {'Summary': summary})
            
            return {
                'success': True,
                'article_number': article_number,
                'article_id': article_id,
                'title': article['title'],
                'message': f'Summary updated for article {article_number}'
            }
        except Exception as e:
            error_msg = str(e)
            # Provide helpful error message for common issues
            if 'ENTITY_IS_LOCKED' in error_msg or 'published' in error_msg.lower():
                return {
                    'success': False, 
                    'error': f'Article {article_number} is published and cannot be edited directly. '
                             'Create a new draft version first.',
                    'original_error': error_msg
                }
            print(f"Error updating KBA summary for article {article_number}: {e}")
            return {'success': False, 'error': error_msg}
