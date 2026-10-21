"""CleverSee UnifiedSearch, explicit tier, no optional paid augmentation."""
from .common import AppError
from .search import TavilySearch,request_once

TIERS={
    'CNLiteBasic':{'label':'国内 · 极速版','coverage':'china','cny_per_1000':8},
    'CNAuto':{'label':'国内 · 智能版','coverage':'china','cny_per_1000':18},
    'GlobalAdvanced':{'label':'全球 · 深度检索','coverage':'global','cny_per_1000':56},
}
PRICE_SOURCE='https://help.aliyun.com/zh/document_detail/3061214.html'
PRICE_AS_OF='2026-10-17'

class AliyunSearch(TavilySearch):
    SCOPE='aliyun-cleversee-unified-bearer-v1'
    PROVIDER='aliyun'
    LABEL='阿里云 CleverSee'
    ENDPOINT='https://cloud-iqs.aliyuncs.com/search/unified'
    def __init__(self,transport=None,cooldown=5,key='',vault=None,engine_type=''):
        super().__init__(transport or (lambda payload,key:request_once(payload,key,self.ENDPOINT)),cooldown,key,vault)
        self.engine_type=engine_type
    @property
    def configured(self):
        return super().configured and self.engine_type in TIERS
    def settings(self):
        value=super().settings();tier=TIERS.get(self.engine_type)
        value.update(configured=self.configured,key_present=bool(self._key),engine_type=self.engine_type,
            tiers=[{'engine_type':key,**item} for key,item in TIERS.items()],coverage=tier['coverage'] if tier else None,
            price_cny_per_request=tier['cny_per_1000']/1000 if tier else None,price_source=PRICE_SOURCE,price_as_of=PRICE_AS_OF,
            optional_paid_augmentation=False)
        if not tier:value.update(status='search_tier_required',message='请选择国内或全球检索档位；尚未发出请求。')
        return value
    def payload(self,query):
        if self.engine_type not in TIERS:raise AppError('请明确选择国内或全球检索档位；没有发出请求',400,'search_tier_required')
        if not 1<=len(query)<=500:raise AppError('阿里云完整查询须为1–500字符，请缩短产品或市场条件；没有发出请求',400,'search_query_too_long')
        payload={'query':query,'engineType':self.engine_type,'contents':{'mainText':False,'markdownText':False}}
        if self.engine_type!='GlobalAdvanced':
            payload['contents'].update(summary=False,rerankScore=False)
            payload['advancedParams']={'numResults':'8'}
        return payload
    def normalize(self,body,query):
        rows=body.get('pageItems')
        if not isinstance(rows,list):raise AppError('阿里云响应缺少 pageItems 列表；未生成候选',502,'search_invalid_response')
        results=[]
        for row in rows[:8]:
            if not isinstance(row,dict) or any(not isinstance(row.get(key),str) for key in ('title','link','snippet')):
                raise AppError('阿里云结果字段格式无效；未生成候选',502,'search_invalid_response')
            results.append({'title':row['title'],'url':row['link'],'content':row['snippet']})
        return {'results':results,'executed_query':query,'provider':self.PROVIDER,'engine_type':self.engine_type,
                'coverage':TIERS[self.engine_type]['coverage'],'price_cny_per_request':TIERS[self.engine_type]['cny_per_1000']/1000}
