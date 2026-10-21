"""Provider-neutral, read-only trade records. No vendor, endpoint or credential default."""
import datetime as dt,math,re
from .common import AppError

NOTICE='历史贸易记录不代表当前采购意向或终端需求；缺失字段、未知覆盖和无匹配均不能解释为没有采购。'
FIELDS=('importer','exporter','product_description','hs_code','origin_country','shipment_country','destination_country','trade_direction','transport_mode','declaration_date','quantity','quantity_unit','value','currency')

def text(value,limit=2000):
    if value is None:return None
    if not isinstance(value,str) or len(value)>limit:raise AppError('海关字段格式无效',502,'customs_invalid_response')
    return value.strip() or None

def date(value):
    value=text(value,10)
    if value is None:return None
    try:return dt.date.fromisoformat(value).isoformat()
    except ValueError:raise AppError('海关日期格式无效',502,'customs_invalid_response') from None

def number(value):
    if value is None:return None
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise AppError('海关数量或金额格式无效',502,'customs_invalid_response')
    return value

def entity(value):
    if value is None:return {'declared_name':None,'enriched_name':None,'name_evidence':'unknown','source_field':None}
    if not isinstance(value,dict):raise AppError('企业名称证据格式无效',502,'customs_invalid_response')
    evidence=value.get('name_evidence','unknown')
    if evidence not in ('source_declaration','source_manifest','supplier_enrichment','unknown'):raise AppError('企业名称证据类型无效',502,'customs_invalid_response')
    declared=text(value.get('declared_name'),300)
    if declared and evidence not in ('source_declaration','source_manifest'):raise AppError('补全名称不能作为实报名称',502,'customs_invalid_response')
    return {'declared_name':declared,'enriched_name':text(value.get('enriched_name'),300),'name_evidence':evidence,'source_field':text(value.get('source_field'),200)}

def record(raw):
    if not isinstance(raw,dict) or not isinstance(raw.get('source'),dict):raise AppError('海关记录缺少来源',502,'customs_invalid_response')
    source=raw['source'];allowed=source.get('authorized_fields')
    if not isinstance(allowed,list) or any(v not in FIELDS for v in allowed):raise AppError('海关记录缺少有效字段授权说明',502,'customs_authorization_missing')
    result={key:None for key in FIELDS}
    for key in FIELDS:
        value=raw.get(key) if key in allowed else None
        if key in ('importer','exporter'):result[key]=entity(value)
        elif key in ('quantity','value'):result[key]=number(value)
        elif key=='declaration_date':result[key]=date(value)
        else:result[key]=text(value)
    if result['trade_direction'] not in (None,'import','export','unknown'):raise AppError('进口/出口方向无效',502,'customs_invalid_response')
    hs=result['hs_code'];verification=raw.get('hs_verification','unknown')
    if hs and (not re.fullmatch(r'\d{2,10}',hs) or verification not in ('source_reported','checked_against_source')):raise AppError('HS须来自可核对源字段，不推断编码',502,'customs_invalid_response')
    result['hs_verification']=verification if hs else 'unknown'
    result['source']={'provider':text(source.get('provider'),100),'record_id':text(source.get('record_id'),200),'source_url':text(source.get('source_url'),2000),'source_date':date(source.get('source_date')),'retrieved_at':text(source.get('retrieved_at'),100),'dataset_direction':text(source.get('dataset_direction'),30),'authorized_fields':allowed}
    if not result['source']['provider'] or not result['source']['record_id']:raise AppError('海关来源缺少供应商或原记录编号',502,'customs_invalid_response')
    return result

class CustomsService:
    def __init__(self,provider=None,today=None,connection=None):self.provider=provider;self.today=today or dt.date.today();self.connection=connection
    def meta(self):
        connected=bool(self.provider and self.provider.configured)
        return {'protocol':'customs-analysis-v1','configured':connected,'status':'configured_not_verified' if connected else 'adapter_required' if self.connection and self.connection.settings()['profile_saved'] else 'provider_not_selected','notice':NOTICE,'max_records':500,'automatic_search':False,'automatic_crm_import':False,'metrics':None,'records':None,'coverage':None}
    def analyze(self,data):
        if not self.meta()['configured']:
            if self.connection and self.connection.settings()['profile_saved']:raise AppError('配置已保存，但供应商协议尚未适配；需要官方文档与响应样例，没有发出查询',503,'customs_adapter_required')
            raise AppError('尚未选择并接入海关供应商；没有发出查询',503,'customs_not_configured')
        if not isinstance(data,dict) or set(data)-{'query','customer_id','consent'} or data.get('consent') is not True:raise AppError('请明确确认查询已授权贸易字段',400,'consent_required')
        query=text(data.get('query'),500)
        if not query:raise AppError('请填写企业、原始产品或已核对的HS查询条件',400,'invalid_input')
        response=self.provider.fetch({'query':query,'limit':500})
        if not isinstance(response,dict) or not isinstance(response.get('records'),list) or len(response['records'])>500:raise AppError('海关供应商响应无效或超限',502,'customs_invalid_response')
        rows=[record(v) for v in response['records']];coverage=response.get('coverage')
        if not isinstance(coverage,dict):coverage={}
        coverage={'complete_for_query':coverage.get('complete_for_query') is True,'date_start':date(coverage.get('date_start')),'date_end':date(coverage.get('date_end')),'country_scope':text(coverage.get('country_scope'),500),'limitations':text(coverage.get('limitations'),2000)}
        monthly={};known=[];terms=[v for v in re.findall(r'[\w\u4e00-\u9fff]+',query.lower()) if len(v)>1][:12]
        for row in rows:
            stamp=row['declaration_date']
            if stamp and stamp<=self.today.isoformat():monthly[stamp[:7]]=monthly.get(stamp[:7],0)+1;known.append(stamp)
            row['match_explanation']={'matched_raw_product_terms':[t for t in terms if t in (row['product_description'] or '').lower()],'basis':'原始产品描述的字面匹配；不是当前需求或适配结论','current_demand':'unknown'}
        trend='unknown';previous=recent=None
        boundary=(self.today-dt.timedelta(days=180)).isoformat();split=(self.today-dt.timedelta(days=90)).isoformat()
        if len(known)==len(rows) and not response.get('truncated') and coverage['complete_for_query'] and coverage['date_start'] and coverage['date_end'] and coverage['date_start']<=boundary and coverage['date_end']>=self.today.isoformat():
            previous=sum(boundary<=v<split for v in known);recent=sum(split<=v<=self.today.isoformat() for v in known);trend='more_records' if recent>previous else 'fewer_records' if recent<previous else 'same_record_count'
        return {'protocol':'customs-analysis-v1','notice':NOTICE,'coverage':coverage,'records':rows,'metrics':{'visible_record_count':len(rows),'latest_declaration_date':max(known) if known else None,'dated_record_count':len(known),'observed_monthly_records':monthly,'frequency_basis':'可见贸易记录数；不是已证明的采购订单次数','trend':trend,'previous_90_days_records':previous,'recent_90_days_records':recent,'current_demand':'unknown','quantity_total':None,'value_total':None},'automatic_crm_import':False,'model_calls':0}
