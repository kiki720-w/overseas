"""Versioned, locally maintained catalog. Documentation verification is not a live test."""
from copy import deepcopy
from .common import AppError

VERSION = '2026-10-04.1'
VERIFIED_AT = '2026-10-04'

def entry(provider, provider_label, model, label, base, scope, adapter, hint, source):
    return dict(option_id=provider+':'+model, label=label, provider=provider,
                provider_label=provider_label, model=model, base_url=base,
                credentialScope=scope, requestAdapter=adapter, key_hint=hint,
                source_url=source)

ITEMS = []
def pair(provider,label,base,scope,hint,source,models):
    for model,title,adapter in models:
        ITEMS.append(entry(provider,label,model,title,base,scope,adapter,hint,source))

pair('qwen-cn','千问','https://dashscope.aliyuncs.com/compatible-mode/v1','dashscope-beijing-standard',
     '阿里云百炼北京地域 API Key；需有对应模型权限。',
     'https://help.aliyun.com/zh/model-studio/deep-thinking',
     [('qwen3.8-max','千问 3.8 Max','qwen'),('qwen3.8-flash','千问 3.8 Flash','qwen')])
pair('deepseek','DeepSeek','https://api.deepseek.com','deepseek-standard',
     'DeepSeek 开放平台 API Key，非其他平台的同名模型 Key。',
     'https://api-docs.deepseek.com/api/create-chat-completion/',
     [('deepseek-v4-pro','DeepSeek V4 Pro','deepseek'),('deepseek-flash','DeepSeek Flash（当前 V4.1）','deepseek')])
pair('kimi-cn','Kimi','https://api.moonshot.cn/v1','moonshot-cn-standard',
     'Kimi 开放平台中国区 API Key，国际区 Key 不通用。',
     'https://platform.kimi.com/docs/api/models-overview',
     [('kimi-k3','Kimi K3','kimi-k3'),('kimi-k2.6','Kimi K2.6','kimi-k2.6')])
pair('glm-cn','智谱','https://open.bigmodel.cn/api/paas/v4','bigmodel-cn-standard',
     '智谱 BigModel 普通 API Key，非 Coding Plan 专用端点。',
     'https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3',
     [('glm-5.3','GLM 5.3','glm'),('glm-5.3-flash','GLM 5.3 Flash','glm')])
ITEMS[-1]['source_url']='https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash'
pair('doubao-cn','豆包','https://ark.cn-beijing.volces.com/api/v3','ark-beijing-standard',
     '火山方舟普通 API Key，并开通对应模型；非 Coding Plan Key。',
     'https://docs.volcengine.com/docs/ark/model-release-announcement?lang=zh',
     [('doubao-seed-2-1-pro-260915','豆包 Seed 2.1 Pro','doubao'),('doubao-seed-2-1-lite-260915','豆包 Seed 2.1 Lite','doubao')])
pair('minimax-cn','MiniMax','https://api.minimax.cn/v1','minimax-cn-standard',
     'MiniMax 中国站普通 API Key，非 M Plan / Code 专用 Key。',
     'https://platform.minimax.cn/docs/api-reference/text-openai-api',
     [('MiniMax-M3','MiniMax M3','minimax-m3'),('MiniMax-M2.7','MiniMax M2.7','minimax-m2.7')])

def catalog():
    return dict(version=VERSION,verified_at=VERIFIED_AT,items=deepcopy(ITEMS),
                notice='精选模型目录依据官方文档核验；权限、额度和实际可用性取决于用户账户，未进行真实连接测试。')

def resolve(data):
    if data.get('catalog_version')!=VERSION:
        raise AppError('模型目录已更新，请刷新后重新选择',409,'catalog_changed')
    for item in ITEMS:
        if data.get('option_id')==item['option_id']:return item
    raise AppError('所选模型不在当前精选目录',400,'catalog_option_invalid')

def match(profile):
    for item in ITEMS:
        if all(profile.get(k)==item[k] for k in ('provider','base_url','model')):return item
    return None

def credential_scope(profile):
    item=match(profile)
    if item:return item['credentialScope']
    # Legacy recognized endpoints retain exactly the same credential boundary.
    legacy={'deepseek':'deepseek-standard','kimi-cn':'moonshot-cn-standard','kimi-global':'moonshot-global-standard'}
    return legacy.get(profile.get('provider'),'custom:'+profile.get('provider','')+':'+profile.get('base_url',''))

def settings_fields(profile):
    item=match(profile)
    return dict(selection_mode='preset' if item else 'custom',option_id=item['option_id'] if item else None,
                catalog_version=VERSION,credentialScope=credential_scope(profile),
                requestAdapter=item['requestAdapter'] if item else 'openai-compatible',
                key_hint=item['key_hint'] if item else '已有自定义连接；请使用该端点所属平台的 Key。')

def payload(profile,messages,task):
    item=match(profile)
    adapter=item['requestAdapter'] if item else 'openai-compatible'
    test=task=='generation_test'
    result={'model':profile['model'],'messages':messages}
    budget=256 if test else 3000
    # No blanket temperature/top_p/n/penalties or reasoning_effort.
    if adapter=='qwen':result.update(max_completion_tokens=budget,enable_thinking=False)
    elif adapter in ('deepseek','kimi-k2.6','doubao'):
        result.update(max_tokens=budget,thinking={'type':'disabled'})
    elif adapter=='kimi-k3':
        result.update(max_tokens=2048 if test else 6000,reasoning_effort='low')
    elif adapter=='glm':
        result.update(max_tokens=2048 if test else 6000,thinking={'type':'enabled'})
    elif adapter=='minimax-m3':
        result.update(max_completion_tokens=budget,thinking={'type':'disabled'},reasoning_split=True)
    elif adapter=='minimax-m2.7':
        result.update(max_tokens=2048 if test else 6000,reasoning_split=True)
    else:result['max_tokens']=budget
    return result
