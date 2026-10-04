# -*- coding: utf-8 -*-
import datetime as dt
import json
import math
import unicodedata

TYPES=['终端加工厂','机床厂家','经销商']
STAGES=['待开发','已联系','需求确认','报价沟通','样品验证','商务谈判','成交','暂缓']
KINDS=['沟通','报价','反馈','跟进']
class AppError(Exception):
    def __init__(self,message,status=400,code='invalid_input'):
        super().__init__(message);self.status=status;self.code=code
class ConflictError(AppError):
    def __init__(self,message='客户资料已在其他页面更新，请刷新核对后再保存；本次未覆盖新资料'):
        super().__init__(message,409,'stale_customer')
def now():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')
def dump(value):return json.dumps(value,ensure_ascii=False)
def field(data,key,limit=200,required=False):
    value=data.get(key,'')
    if not isinstance(value,str) or len(value)>limit:raise AppError(f'{key}必须是文本，最多{limit}字符')
    value=value.strip()
    if required and not value:raise AppError(f'{key}不能为空')
    return value
def norm(value):return ' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())
def paging(query):
    try:p=int(query.get('page',1));s=int(query.get('page_size',25))
    except (ValueError,TypeError):raise AppError('分页参数必须为整数')
    if p<1 or s<1 or s>100:raise AppError('页码至少1，每页数量1至100')
    return p,s
def page_result(items,total,page,size):return {'items':items,'total':total,'page':page,'page_size':size,'pages':max(1,math.ceil(total/size))}
def customer_data(data):
    c={k:field(data,k,limit,req) for k,limit,req in [('name',200,True),('type',30,True),('country',100,False),('product',500,False),('source',2000,False),('source_date',10,False),('facts',4000,False),('hypotheses',4000,False),('unknowns',4000,False),('signals',4000,False),('stage',30,True),('blocker',2000,False),('website',500,False),('email',200,False),('contact_name',200,False)]}
    if c['type'] not in TYPES or c['stage'] not in STAGES:raise AppError('客户类型或阶段无效')
    if c['source_date']:
        try:dt.date.fromisoformat(c['source_date'])
        except ValueError:raise AppError('来源日期应为有效YYYY-MM-DD')
    if type(data.get('priority',False)) is not bool:raise AppError('重点客户必须为布尔值')
    c['priority']=data.get('priority',False)
    if c['email'] and ('@' not in c['email'] or ' ' in c['email']):raise AppError('邮箱格式无效')
    return c
