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
            
            logger.info(f"DEBUG SOSL: {sosl}")

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
            
            logger.info(f"DEBUG SOQL Fuzzy: {query}")
            
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            logger.error(f"Error in fuzzy SOQL search: {e}")
            return []

    def search_knowledge_articles(self, subject: str, description: str) -> List[Dict[str, Any]]:
        """
        Search knowledge articles using SOSL based on subject + description.
        Returns a concise list of article records.
        """
        self.connect()
        try:
            combined = " ".join([subject or "", description or ""]).strip()
            if not combined:
                return []

            # Limit search size to avoid SOSL length issues
            combined = combined[:400]
            escaped_query = self._escape_sosl(combined)
            sosl = (
                "FIND {{{query}}} IN ALL FIELDS RETURNING "
                "Knowledge__kav(Id, Title, UrlName, Summary, LastModifiedDate), "
                "KnowledgeArticleVersion(Id, Title, UrlName, Summary, LastModifiedDate)"
            ).format(query=escaped_query)

            logger.info(f"DEBUG SOSL Knowledge: {sosl}")
            result = self.sf.search(sosl)
            records = result.get("searchRecords", [])

            articles = []
            for r in records:
                attrs = r.get("attributes", {})
                articles.append({
                    "id": r.get("Id"),
                    "title": r.get("Title"),
                    "url_name": r.get("UrlName"),
                    "summary": r.get("Summary"),
                    "last_modified_date": r.get("LastModifiedDate"),
                    "object_type": attrs.get("type")
                })
            return articles
        except Exception as e:
            logger.error(f"Error searching knowledge articles: {e}")
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

    def get_case_emails(self, case_id: str) -> List[Dict[str, Any]]:
        """Fetch email messages related to the case"""
        self.connect()
        try:
            query = f"""
                SELECT Subject, FromAddress, ToAddress, CcAddress, BccAddress,
                       TextBody, HtmlBody, CreatedDate, MessageDate, Incoming
                FROM EmailMessage
                WHERE ParentId = '{case_id}'
                ORDER BY CreatedDate DESC
                LIMIT 20
            """
            result = self.sf.query(query)
            return result.get('records', [])
        except Exception as e:
            logger.error(f"Error fetching emails for {case_id}: {e}")
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
        emails = self.get_case_emails(case_id)
        
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
            'feed_items': feed[:10],  # Last 10 feed items
            'emails': emails[:5]  # Last 5 emails
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
        emails = self.get_case_emails(case_id)
        related = self.get_related_cases(case_id, case['Subject'])
        articles = self.get_case_articles(case_id)
        
        result['history'] = history[:10]
        result['recent_comments'] = comments[:5]
        result['feed_items'] = feed[:10]
        result['emails'] = emails[:10]
        result['related_cases'] = related[:5]
        result['knowledge_articles'] = articles
        
        # Calculate metrics for insights
        result['metrics'] = {
            'total_comments': len(comments),
            'total_history_changes': len(history),
            'related_cases_count': len(related),
            'articles_count': len(articles),
            'has_recent_activity': days_since_update is not None and days_since_update < 7,
            'days_since_update': days_since_update,
            'emails_count': len(emails)
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
        
        Describes: Case, CaseComment, Knowledge__kav
        
        Returns:
            Dictionary with metadata for each workflow object
        """
        workflow_objects = ['Case', 'CaseComment']
        
        result = {}
        for obj_name in workflow_objects:
            result[obj_name] = self.describe_sobject(obj_name)
        
        # Try Knowledge__kav (may not exist in all orgs)
        try:
            result['Knowledge__kav'] = self.describe_sobject('Knowledge__kav')
        except:
            result['Knowledge__kav'] = {'error': 'Knowledge not enabled in this org'}
        
        return result

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
            Confirmation of update with changed fields and contextual next actions
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
            
            # Determine contextual next actions based on what was updated
            new_status = fields.get('Status', '')
            
            if new_status in ['Closed', 'Resolved']:
                # Case closure workflow
                next_actions = [
                    "Create Knowledge Article from this resolution",
                    "Add final internal summary comment"
                ]
            elif 'Fix_Status__c' in fields or 'Validation_Status__c' in fields:
                # Technical status update
                next_actions = [
                    "Add internal note about the progress",
                    "Close the case if issue is fully resolved"
                ]
            else:
                # General update
                next_actions = [
                    "Add internal note about the change",
                    "Update case status if appropriate"
                ]
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case_id,
                'updated_fields': list(fields.keys()),
                'new_values': fields,
                'message': f'Case {case_number} updated successfully',
                'next_actions': next_actions
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
            Confirmation of comment added with contextual next actions
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
            
            # Determine next actions based on comment type
            if is_public:
                next_actions = [
                    "Update case status if appropriate",
                    "Close the case if issue is resolved"
                ]
            else:
                next_actions = [
                    "Update case status or technical fields",
                    "Add more details or follow-up notes if needed"
                ]
            
            return {
                'success': True,
                'case_number': case_number,
                'case_id': case_id,
                'comment_id': result.get('id'),
                'is_public': is_public,
                'message': f'{"Public" if is_public else "Internal"} comment added to case {case_number}',
                'next_actions': next_actions
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
        import time
        self.connect()
        try:
            # Generate URL name if not provided - include timestamp to ensure uniqueness
            if not url_name:
                base_url = title.lower().replace(' ', '-').replace('/', '-')[:40]
                # Remove special characters
                base_url = ''.join(c for c in base_url if c.isalnum() or c == '-')
                # Add timestamp to ensure uniqueness
                url_name = f"{base_url}-{int(time.time())}"
            else:
                # If url_name provided, sanitize it
                url_name = ''.join(c for c in url_name if c.isalnum() or c == '-')[:50]
            
            # Create Knowledge Article (Draft) with all required fields
            article_data = {
                'Title': title,
                'Summary': summary,
                'UrlName': url_name,
                'Language': 'en_US',  # Required field
                'IsVisibleInPkb': True,  # Public Knowledge Base visibility
                'IsVisibleInCsp': True,  # Customer Portal visibility
                'IsVisibleInPrm': False,  # Partner Portal visibility
            }
            
            # Try to create the article with retry on duplicate UrlName
            max_retries = 3
            article_id = None
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    result = self.sf.Knowledge__kav.create(article_data)
                    article_id = result.get('id')
                    break
                except Exception as kav_error:
                    last_error = kav_error
                    error_str = str(kav_error)
                    
                    # Check if it's a duplicate UrlName error - retry with new unique suffix
                    if 'DUPLICATE_VALUE' in error_str and 'UrlName' in error_str:
                        # Generate new unique UrlName with timestamp + attempt
                        base_url = title.lower().replace(' ', '-').replace('/', '-')[:35]
                        base_url = ''.join(c for c in base_url if c.isalnum() or c == '-')
                        url_name = f"{base_url}-{int(time.time())}-{attempt + 1}"
                        article_data['UrlName'] = url_name
                        continue
                    else:
                        # Non-duplicate error, don't retry
                        break
            
            # If all retries failed
            if article_id is None:
                return {
                    'success': False,
                    'error': f'Could not create Knowledge Article after {max_retries} attempts. Last error: {str(last_error)}'
                }
            
            # Link to case if provided
            # Note: article_id is the version ID (ka0...), but CaseArticle needs the master KnowledgeArticleId (kav...)
            master_article_id = None
            linked_successfully = False
            if case_number and article_id:
                try:
                    # Query to get the master KnowledgeArticleId from the version record
                    kav_query = f"SELECT KnowledgeArticleId FROM Knowledge__kav WHERE Id = '{article_id}'"
                    kav_result = self.sf.query(kav_query)
                    if kav_result['totalSize'] > 0:
                        master_article_id = kav_result['records'][0]['KnowledgeArticleId']
                    
                    case = self.get_case(case_number)
                    if case and master_article_id:
                        self.sf.CaseArticle.create({
                            'CaseId': case['Id'],
                            'KnowledgeArticleId': master_article_id
                        })
                        linked_successfully = True
                except Exception as link_error:
                    logger.warning(f"Could not link article to case: {link_error}")
            
            # Determine next actions - KBA creation is often one of the final steps
            if case_number and linked_successfully:
                next_actions = [
                    "Workflow complete - case has KBA linked, no further actions required",
                    "Optionally: Close the case if not already closed",
                    "Optionally: Add final internal summary comment"
                ]
            else:
                next_actions = [
                    "Link this article to relevant cases if needed",
                    "Publish the article in Salesforce (currently in Draft status)",
                    "Create additional articles for related topics"
                ]
            
            return {
                'success': True,
                'article_id': article_id,
                'title': title,
                'url_name': url_name,
                'linked_case': case_number,
                'status': 'Draft',
                'message': f'Knowledge Article "{title}" created successfully. Status: Draft (needs publishing).',
                'next_actions': next_actions
            }
        except Exception as e:
            logger.error(f"Error creating knowledge article: {e}")
            return {'success': False, 'error': str(e)}
