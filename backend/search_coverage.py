# -*- coding: utf-8 -*-
"""Public, inspectable coverage; no CRM or factory contents enter queries."""
import hashlib
from .search import compose_query

def fingerprint(request):
    return ' '.join(compose_query(request).casefold().split())

def expand(base, regions, industries, synonyms, inferred):
    products=list(dict.fromkeys([base['products']]+synonyms+inferred))
    contexts=[('', '')]+[(r,'') for r in regions]+[('',i) for i in industries]+[(r,i) for r in regions for i in industries]
    intents=[('官方网站与公司介绍','company official website about us'),('公开产品与目录','products catalogue'),
             ('公开制造能力','manufacturing capabilities'),('公开设备与生产工艺','equipment production processes'),
             ('公开应用与行业案例','applications industries case studies'),('公开联系方式与企业身份','company contact location')]
    for product in products:
        for region,industry in contexts:
            for label,intent in intents:
                parts=[base['query']]
                if product and product!=base['products']:parts.append('product term: '+product)
                if region:parts.append('region: '+region)
                if industry:parts.append('industry: '+industry)
                parts.append(intent)
                request=dict(base,query=' | '.join(parts),products=product)
                if len(request['query'])<=500:
                    yield request,' / '.join(filter(None,[label,product,region,industry]))

def proposal(plan):
    if not plan['coverage']:return None
    request,angle=plan['coverage'][0]
    token=hashlib.sha256((plan['plan_id']+'|'+str(len(plan['queries']))+'|'+fingerprint(request)).encode()).hexdigest()[:32]
    return {'index':len(plan['queries']),'query':request['query'],'executed_query':plan.get('query_builder',compose_query)(request),'angle':angle,'extension_token':token}
