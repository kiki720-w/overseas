"""Configurable seller-product to buyer-process search previews; no buyer conclusions."""
from .application_recall import CONCEPTS
from .common import AppError,norm

PROFILES={
    'gear_hobbing_workholding':['gear hobbing services','hobbed gear manufacturer'],
    'gear_workholding':['precision gear machining services','gear hobbing services','gear grinding services'],
    'machining_center_workholding':['CNC machining services','precision CNC machine shop'],
}


def resolve(data):
    config=data.get('buyer_intent',{})
    if not isinstance(config,dict) or set(config)-{'target','profile','process_terms'}:raise AppError('买家用途配置格式无效',400,'invalid_buyer_intent')
    target=config.get('target','terminal_processor')
    if target not in ('terminal_processor','as_entered'):raise AppError('买家用途目标无效',400,'invalid_buyer_intent')
    terms=config.get('process_terms',[])
    if not isinstance(terms,list) or len(terms)>4 or any(not isinstance(t,str) or not 2<=len(t.strip())<=64 or any(ord(c)<32 for c in t) for t in terms):raise AppError('请填写至多4项买家工艺或用途，每项2–64字符',400,'invalid_buyer_intent')
    profile=config.get('profile')
    if profile is not None and (not isinstance(profile,str) or profile not in PROFILES):raise AppError('未知买家用途配置，请填写工艺词',400,'invalid_buyer_intent')
    product=norm(data.get('products',''))
    if profile is None and product:
        profile=next((c['id'] for c in CONCEPTS if c['id'] in PROFILES and any(norm(a) in product for a in c['factory_aliases'])),None)
    allowed=not data.get('type') or data['type']=='终端加工厂'
    enabled=target!='as_entered' and allowed and bool(terms or profile)
    chosen=list(dict.fromkeys(t.strip() for t in terms)) if terms else list(PROFILES.get(profile,[]))
    return {'enabled':enabled,'target':target,'profile':profile,'process_terms':chosen if enabled else [],
            'original_query':data['query'],'seller_product':data.get('products',''),
            'basis':'user_configured_processes' if terms else 'editable_process_profile' if enabled else 'as_entered_unmapped',
            'notice':'按买方加工用途构造公开查询；查询词和搜索命中不证明工厂身份、适配性或采购需求。' if enabled else '未套用买家用途映射；请核对查询是否在寻找同行，未知行业可手动配置 process_terms。'}


def request(base,intent,term=None):
    if not intent['enabled']:return dict(base)
    # Do not append the seller's fixture product or a fixture-manufacturer qualifier.
    return dict(base,query=(term or intent['process_terms'][0])+' company official website',products='',type='')


def localized_requests(base,intent,countries):
    # Explicit vocabulary only; no model translation or inferred purchasing intent.
    translations={
        'gear hobbing services':('滚齿 加工服务 工厂 官网','Lohnverzahnung Wälzfräsen Unternehmen'),
        'hobbed gear manufacturer':('齿轮 滚齿 加工 厂家 官网','Zahnradhersteller Wälzfräsen Unternehmen'),
        'precision gear machining services':('精密齿轮 加工服务 工厂 官网','Präzisionszahnräder Lohnfertigung Unternehmen'),
        'gear grinding services':('磨齿 齿轮加工 服务 工厂 官网','Zahnradschleifen Lohnfertigung Unternehmen'),
        'CNC machining services':('数控 加工服务 工厂 官网','CNC Lohnfertigung Unternehmen'),
        'precision CNC machine shop':('精密 数控 机械加工 工厂 官网','Präzision CNC Zerspanung Unternehmen'),
    }
    market=norm(countries)
    languages=[]
    if any(v in market for v in ('中国','china','cn')):languages.append((0,'中文买方加工用途'))
    if any(v in market for v in ('德国','germany','deutschland')):languages.append((1,'德语买方加工用途'))
    for term in intent['process_terms']:
        if term not in translations:continue
        for index,label in languages:
            yield dict(base,query=translations[term][index],products='',type=''),label+' / '+term
