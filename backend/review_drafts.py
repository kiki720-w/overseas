"""Current-session unreviewed drafts. No database, token minting or approval."""
import copy,datetime as dt,hashlib,json,re,secrets,threading,time
from .common import AppError
PROTOCOL='deal-review-draft-v4'
PERMISSIONS=dict(view=True,edit=True,discard=True,send=False,approve=False,approved_export=False,publish=False)
def fail(code,message,status=400):raise AppError(message,status,code)
def valid_session(session):
    if not isinstance(session,str) or not re.fullmatch('[a-f0-9]{32}',session):fail('review_scope_mismatch','需要当前授权会话',403)
def input_shape(data,keys):
    if not isinstance(data,dict) or set(data)!=set(keys):fail('invalid_review_input','草稿请求字段不完整或含额外字段')
    if not isinstance(data['customer_id'],str) or not 1<=len(data['customer_id'])<=32:fail('invalid_review_input','客户范围无效')
    if not isinstance(data['draft_id'],str) or not re.fullmatch('[a-f0-9]{32}',data['draft_id']):fail('invalid_review_input','草稿标识无效')
    if 'expected_draft_version' in data and (type(data['expected_draft_version']) is not int or data['expected_draft_version']<1):fail('invalid_review_input','草稿版本无效')
def scope_fingerprint(data):
    scope={k:data.get(k) for k in ('deal_question','context_token','revision')};scope['knowledge_selections']=data.get('knowledge_selections',[])
    return hashlib.sha256(json.dumps(scope,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class ReviewDrafts:
    def __init__(self,current,clock=None,utcnow=None,capacity=100,ttl=1800):
        self.current=current;self.clock=clock or time.monotonic;self.utcnow=utcnow or (lambda:dt.datetime.now(dt.timezone.utc))
        self.capacity=capacity;self.ttl=ttl;self.rows={};self.expired={};self.lock=threading.RLock()
    def _fresh(self,row):
        try:now=self.current(row['public']['customer_id'],row['selections'],row['public']['scope']['question'])
        except (AppError,LookupError,ValueError):fail('review_context_changed','客户或来源已变化，请重新预览',409)
        scope=row['public']['scope']
        if any(now.get(k)!=scope[k] for k in ('customer_version','profile_revision','context_token')):fail('review_context_changed','客户、来源或模型版本已变化，请重新预览',409)
    def _owned(self,cid,did,session,check_current=True):
        valid_session(session)
        row=self.rows.get(did)
        if not row:
            old=self.expired.get(did)
            if old:
                if old[:2]!=(cid,session):fail('review_scope_mismatch','草稿不属于当前会话或客户',403)
                fail('review_draft_expired','草稿已过期，请重新预览',410)
            fail('review_draft_not_found','草稿不存在或已丢弃',404)
        if row['session']!=session or row['public']['customer_id']!=cid:fail('review_scope_mismatch','草稿不属于当前会话或客户',403)
        if self.clock()>=row['deadline']:fail('review_draft_expired','草稿已过期，请重新预览',410)
        if check_current:self._fresh(row)
        return row
    def create(self,cid,session,data,scope,candidate,review,prepare_id=None):
        valid_session(session)
        if any(type(scope.get(k)) is not int for k in ('customer_version','profile_revision')) or type(data.get('revision')) is not int:fail('invalid_review_input','上下文版本必须为整数')
        if scope.get('context_token')!=data.get('context_token') or scope.get('profile_revision')!=data.get('revision'):fail('review_context_changed','生成范围已变化，请重新预览',409)
        public={'protocol':PROTOCOL,'status':'needs_review','customer_id':cid,'draft_id':secrets.token_hex(16),'draft_version':1,
            'expires_at':(self.utcnow()+dt.timedelta(seconds=self.ttl)).isoformat(),'original':copy.deepcopy(candidate),'edited_draft':None,'edited':False,
            'scope':dict(scope,question=data['deal_question'],request_id=data['request_id'],prepare_id=prepare_id),'review':copy.deepcopy(review),
            'permissions':dict(PERMISSIONS),'evidence_status':'unreviewed','approved':False,'unsent':True,
            'notice':'未审核草稿；编辑不确认事实，不授予发送、批准或正式导出权限。'}
        # Bounded authorized material only; never persist profile/credential objects.
        if len(json.dumps(public,ensure_ascii=False).encode())>196608:fail('invalid_review_input','待审草稿超过大小限制',413)
        row={'public':public,'session':session,'deadline':self.clock()+self.ttl,'selections':copy.deepcopy(data.get('knowledge_selections',[])),
            'fingerprint':scope_fingerprint(data)}
        with self.lock:
            self._fresh(row)
            for did,old in list(self.rows.items()):
                if self.clock()>=old['deadline']:
                    self.expired[did]=(old['public']['customer_id'],old['session']);del self.rows[did]
            while len(self.expired)>self.capacity:self.expired.pop(next(iter(self.expired)))
            if len(self.rows)>=self.capacity:fail('review_draft_capacity','待审草稿容量已满，请丢弃旧草稿',429)
            self.rows[public['draft_id']]=row
        return copy.deepcopy(public)
    def replay(self,cid,session,data):
        valid_session(session)
        with self.lock:
            for did,row in self.rows.items():
                if row['session']==session and row['public']['customer_id']==cid and row['public']['scope']['request_id']==data.get('request_id'):
                    if row['fingerprint']!=scope_fingerprint(data):fail('request_conflict','同一请求标识的范围已变化',409)
                    return copy.deepcopy(self._owned(cid,did,session)['public'])
        return None
    def view(self,data,session):
        input_shape(data,('customer_id','draft_id'))
        with self.lock:return copy.deepcopy(self._owned(data['customer_id'],data['draft_id'],session)['public'])
    def edit(self,data,session):
        input_shape(data,('customer_id','draft_id','expected_draft_version','draft'))
        text=data['draft']
        if not isinstance(text,str) or len(text)>6000 or any(ord(c)<32 and c not in '\n\r\t' for c in text):fail('invalid_review_input','草稿必须为不超过6000字符的纯文本')
        with self.lock:
            row=self._owned(data['customer_id'],data['draft_id'],session);p=row['public']
            if data['expected_draft_version']!=p['draft_version']:fail('review_draft_conflict','草稿版本冲突，请刷新',409)
            from .deal_semantic_contract import audit_prose
            from .deal_semantic_contract import reject_sensitive_content
            reject_sensitive_content(text)
            issues=[i for i in p['review']['issues'] if not i['path'].startswith('edited_draft')]
            issues.extend(audit_prose(text,'edited_draft'))
            issues.append({'path':'edited_draft','category':'user_edit_requires_review','reason':'Editing never establishes evidence or approval.','original_text':text})
            changed=copy.deepcopy(p)
            changed['edited_draft']=text;changed['edited']=True;changed['draft_version']+=1;changed['review']['issues']=issues;changed['review']['edited_content_fact_checked']=False
            if len(json.dumps(changed,ensure_ascii=False).encode())>196608:fail('invalid_review_input','待审草稿超过大小限制',413)
            row['public']=changed
            return copy.deepcopy(changed)
    def discard(self,data,session):
        input_shape(data,('customer_id','draft_id','expected_draft_version'))
        with self.lock:
            row=self._owned(data['customer_id'],data['draft_id'],session,check_current=False)
            if data['expected_draft_version']!=row['public']['draft_version']:fail('review_draft_conflict','草稿版本冲突，请刷新',409)
            del self.rows[data['draft_id']]
            return {'protocol':PROTOCOL,'status':'discarded','customer_id':data['customer_id'],'draft_id':data['draft_id']}
