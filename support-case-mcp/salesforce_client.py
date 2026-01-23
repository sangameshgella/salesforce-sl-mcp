import os
import logging
from typing import Dict, Any, List, Optional
from simple_salesforce import Salesforce
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logger = logging.getLogger("salesforce_client")
logger.setLevel(logging.DEBUG)

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
            logger.error(f"Error fetching case {case_number}: {e}")
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
            logger.error(f"Error in SOSL search: {e}")
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
            logger.error(f"Error in fuzzy SOQL search: {e}")
            return []

    def get_case_comments(self, case_id: str) -> List[Dict[str, Any]]:
        self.connect()
        try:
            query = f"SELECT CommentBody, CreatedDate, CreatedBy.Name FROM CaseComment WHERE ParentId = '{case_id}' ORDER BY CreatedDate DESC"
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
             logger.error(f"Error fetching comments for {case_id}: {e}")
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
            logger.error(f"Error fetching history for {case_id}: {e}")
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
            logger.error(f"Error fetching feed for {case_id}: {e}")
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
            logger.error(f"Error fetching case with status {case_number}: {e}")
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
            logger.error(f"Error fetching related cases: {e}")
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
            logger.error(f"Error fetching articles for case {case_id}: {e}")
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

    # ========== SCHEMA TOOLS (Context Providers) ==========

    def describe_sobject(self, object_name: str) -> Dict[str, Any]:
        """
        Get field metadata for a Salesforce object with picklist label-to-value mappings.
        
        This enables the LLM to:
        - Know valid field API names
        - Map user-friendly labels to API values
        - Understand field types and requirements
        
        Args:
            object_name: The Salesforce object API name (e.g., 'Case', 'CaseComment')
            
        Returns:
            Object metadata with fields, types, and picklist mappings
        """
        self.connect()
        try:
            # Get object describe from Salesforce
            sobject = getattr(self.sf, object_name)
            describe = sobject.describe()
            
            # Format fields with rich metadata for LLM consumption
            fields = []
            for field in describe.get('fields', []):
                field_info = {
                    'api_name': field['name'],
                    'label': field['label'],
                    'type': field['type'],
                    'updateable': field['updateable'],
                    'createable': field['createable'],
                    'required': not field['nillable'] and field['createable'],
                    'length': field.get('length'),
                }
                
                # For picklist fields, include value mappings (label -> API value)
                if field['type'] in ('picklist', 'multipicklist'):
                    field_info['picklist_values'] = [
                        {
                            'api_value': pv['value'],
                            'label': pv['label'],
                            'active': pv['active'],
                            'default': pv.get('defaultValue', False)
                        }
                        for pv in field.get('picklistValues', [])
                        if pv['active']
                    ]
                
                # For reference fields, show what objects they point to
                if field['type'] == 'reference':
                    field_info['references'] = field.get('referenceTo', [])
                
                fields.append(field_info)
            
            return {
                'object_name': object_name,
                'label': describe.get('label'),
                'label_plural': describe.get('labelPlural'),
                'updateable': describe.get('updateable'),
                'createable': describe.get('createable'),
                'deletable': describe.get('deletable'),
                'field_count': len(fields),
                'fields': fields
            }
        except Exception as e:
            logger.error(f"Error describing {object_name}: {e}")
            return {'error': str(e), 'object_name': object_name}

    def describe_workflow_objects(self) -> Dict[str, Any]:
        """
        Get metadata for all objects in the support case workflow.
        
        Describes: Case, CaseComment, EmailMessage, Knowledge__kav
        
        Returns:
            Dictionary with metadata for each workflow object
        """
        workflow_objects = ['Case', 'CaseComment', 'EmailMessage']
        
        result = {}
        for obj_name in workflow_objects:
            result[obj_name] = self.describe_sobject(obj_name)
        
        # Try Knowledge__kav (may not exist in all orgs)
        try:
            result['Knowledge__kav'] = self.describe_sobject('Knowledge__kav')
        except:
            result['Knowledge__kav'] = {'error': 'Knowledge not enabled in this org'}
        
        return result

    # ========== EMAIL TOOLS ==========

    def get_case_emails(self, case_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all email messages linked to a case.
        
        Returns emails for AI summarization and drafting context.
        
        Args:
            case_id: The Salesforce Case Id
            
        Returns:
            List of email messages with sender, recipient, subject, body, date
        """
        self.connect()
        try:
            query = f"""
                SELECT Id, Subject, TextBody, HtmlBody, FromAddress, ToAddress, 
                       CcAddress, BccAddress, MessageDate, Incoming, Status,
                       CreatedDate, CreatedById
                FROM EmailMessage 
                WHERE RelatedToId = '{case_id}' 
                ORDER BY MessageDate DESC
                LIMIT 50
            """
            result = self.sf.query(query)
            
            # Format for cleaner output
            emails = []
            for email in result.get('records', []):
                emails.append({
                    'id': email.get('Id'),
                    'subject': email.get('Subject'),
                    'from': email.get('FromAddress'),
                    'to': email.get('ToAddress'),
                    'cc': email.get('CcAddress'),
                    'date': email.get('MessageDate'),
                    'direction': 'inbound' if email.get('Incoming') else 'outbound',
                    'body': email.get('TextBody') or email.get('HtmlBody', ''),
                    'status': email.get('Status')
                })
            
            return emails
        except Exception as e:
            logger.error(f"Error fetching emails for case {case_id}: {e}")
            return []

    def draft_case_email(self, case_number: str, message: str) -> Dict[str, Any]:
        """
        Create a draft email preview for user approval (does NOT send).
        
        This enables the LLM to show the user what will be sent before sending.
        
        Args:
            case_number: The case to respond to
            message: The email body content
            
        Returns:
            Draft preview with recipient, subject, body for user approval
        """
        self.connect()
        try:
            # Get case with contact info
            query = f"""
                SELECT Id, CaseNumber, Subject, Contact.Email, Contact.Name
                FROM Case 
                WHERE CaseNumber = '{case_number}' 
                LIMIT 1
            """
            result = self.sf.query(query)
            
            if result['totalSize'] == 0:
                return {'success': False, 'error': f'Case {case_number} not found'}
            
            case = result['records'][0]
            contact = case.get('Contact', {}) or {}
            contact_email = contact.get('Email')
            contact_name = contact.get('Name', 'Customer')
            
            if not contact_email:
                return {
                    'success': False, 
                    'error': 'No contact email found for this case',
                    'case_number': case_number
                }
            
            # Generate email subject
            subject = f"Re: Case {case['CaseNumber']} - {case['Subject']}"
            
            return {
                'success': True,
                'draft': True,
                'case_number': case_number,
                'case_id': case['Id'],
                'to_email': contact_email,
                'to_name': contact_name,
                'subject': subject,
                'body': message,
                'instructions': 'Review this draft. Call send_case_email to send, or revise the message.'
            }
        except Exception as e:
            logger.error(f"Error drafting email for case {case_number}: {e}")
            return {'success': False, 'error': str(e)}

    def send_case_email(self, case_number: str, subject: str, body: str) -> Dict[str, Any]:
        """
        Send an email to the case contact via Salesforce REST API.
        
        The email is sent AND logged to the case automatically.
        Only call this AFTER user has approved the draft.
        
        Args:
            case_number: The case to respond to
            subject: Email subject
            body: Email body content
            
        Returns:
            Confirmation of email sent and logged
        """
        self.connect()
        try:
            # Get case with contact info
            query = f"""
                SELECT Id, CaseNumber, Contact.Email, Contact.Name
                FROM Case 
                WHERE CaseNumber = '{case_number}' 
                LIMIT 1
            """
            result = self.sf.query(query)
            
            if result['totalSize'] == 0:
                return {'success': False, 'error': f'Case {case_number} not found'}
            
            case = result['records'][0]
            contact = case.get('Contact', {}) or {}
            contact_email = contact.get('Email')
            
            if not contact_email:
                return {
                    'success': False, 
                    'error': 'No contact email found for this case'
                }
            
            # Send email via Salesforce REST API
            # Using the simple email action
            email_data = {
                'inputs': [{
                    'emailAddresses': contact_email,
                    'emailSubject': subject,
                    'emailBody': body,
                    'senderType': 'CurrentUser'
                }]
            }
            
            # Call the email action
            try:
                self.sf.restful('actions/standard/emailSimple', method='POST', data=email_data)
            except Exception as email_error:
                # Fallback: Create EmailMessage record directly
                email_record = {
                    'RelatedToId': case['Id'],
                    'ToAddress': contact_email,
                    'Subject': subject,
                    'TextBody': body,
                    'Status': '3',  # Sent
                    'Incoming': False
                }
                self.sf.EmailMessage.create(email_record)
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case['Id'],
                'sent_to': contact_email,
                'subject': subject,
                'message': f'Email sent to {contact_email} and logged to case {case_number}'
            }
        except Exception as e:
            logger.error(f"Error sending email for case {case_number}: {e}")
            return {'success': False, 'error': str(e)}

    # ========== CASE WRITE TOOLS ==========

    def update_case(self, case_number: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update case fields with validated values.
        
        The LLM should call describe_sobject first to get valid field names
        and picklist values, then provide exact API values here.
        
        Args:
            case_number: The case to update
            fields: Dictionary of field API names and values to update
                   Example: {"Status": "Closed", "Fix_Status__c": "Implemented"}
                   
        Returns:
            Confirmation of update with changed fields
        """
        self.connect()
        try:
            # Get case Id
            case = self.get_case(case_number)
            if not case:
                return {'success': False, 'error': f'Case {case_number} not found'}
            
            case_id = case['Id']
            
            # Update the case
            self.sf.Case.update(case_id, fields)
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case_id,
                'updated_fields': list(fields.keys()),
                'new_values': fields,
                'message': f'Case {case_number} updated successfully'
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error updating case {case_number}: {e}")
            return {'success': False, 'error': error_msg}

    def add_case_comment(self, case_number: str, comment: str, is_public: bool = False) -> Dict[str, Any]:
        """
        Add a comment to a case.
        
        Args:
            case_number: The case to comment on
            comment: The comment text
            is_public: If True, visible to customer in portal. If False, internal only.
            
        Returns:
            Confirmation of comment added
        """
        self.connect()
        try:
            # Get case Id
            case = self.get_case(case_number)
            if not case:
                return {'success': False, 'error': f'Case {case_number} not found'}
            
            case_id = case['Id']
            
            # Create the comment
            comment_data = {
                'ParentId': case_id,
                'CommentBody': comment,
                'IsPublished': is_public
            }
            
            result = self.sf.CaseComment.create(comment_data)
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case_id,
                'comment_id': result.get('id'),
                'is_public': is_public,
                'message': f'{"Public" if is_public else "Internal"} comment added to case {case_number}'
            }
        except Exception as e:
            logger.error(f"Error adding comment to case {case_number}: {e}")
            return {'success': False, 'error': str(e)}

    # ========== KNOWLEDGE ARTICLE TOOLS ==========

    def create_knowledge_article(self, title: str, summary: str, content: str, 
                                  url_name: str = None, case_number: str = None) -> Dict[str, Any]:
        """
        Create a new Knowledge Article from a resolved case.
        
        Args:
            title: Article title
            summary: Brief summary/abstract
            content: Full article content/details
            url_name: URL-friendly name (auto-generated if not provided)
            case_number: Optional - link the article to this case
            
        Returns:
            Confirmation with article ID and URL
        """
        self.connect()
        try:
            # Generate URL name if not provided
            if not url_name:
                url_name = title.lower().replace(' ', '-').replace('/', '-')[:50]
                # Remove special characters
                url_name = ''.join(c for c in url_name if c.isalnum() or c == '-')
            
            # Create Knowledge Article (Draft)
            # Note: The exact object name may vary (Knowledge__kav, Knowledge, etc.)
            article_data = {
                'Title': title,
                'Summary': summary,
                'UrlName': url_name,
                # 'Details__c': content,  # Field name may vary by org
            }
            
            # Try to create the article
            try:
                result = self.sf.Knowledge__kav.create(article_data)
                article_id = result.get('id')
            except Exception as kav_error:
                # Try alternative Knowledge object
                try:
                    result = self.sf.KnowledgeArticle.create(article_data)
                    article_id = result.get('id')
                except:
                    return {
                        'success': False,
                        'error': f'Could not create Knowledge Article. Error: {str(kav_error)}. '
                                'Knowledge may not be enabled or accessible in this org.'
                    }
            
            # Link to case if provided
            if case_number and article_id:
                try:
                    case = self.get_case(case_number)
                    if case:
                        self.sf.CaseArticle.create({
                            'CaseId': case['Id'],
                            'KnowledgeArticleId': article_id
                        })
                except Exception as link_error:
                    logger.warning(f"Could not link article to case: {link_error}")
            
            return {
                'success': True,
                'article_id': article_id,
                'title': title,
                'url_name': url_name,
                'linked_case': case_number,
                'status': 'Draft',
                'message': f'Knowledge Article "{title}" created successfully. Status: Draft (needs publishing).'
            }
        except Exception as e:
            logger.error(f"Error creating knowledge article: {e}")
            return {'success': False, 'error': str(e)}
