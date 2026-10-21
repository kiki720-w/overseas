"""Local evidence sufficiency labels; no requests or company conclusions."""
import re


def assess(content):
    text=content.strip()
    lines=[re.sub(r'^[#*\->\s]+','',line).strip() for line in text.splitlines() if line.strip()]
    navigation=re.compile(r'contact|career|sign in|log in|cookie|privacy|menu|all products|latest news|select.*region|联系我们|联系\s*SCHUNK|职业|地区|请求|最新资讯|全系列产品|产品$|首页|登录|隐私',re.I)
    prose=[line for line in lines if len(line)>=60 and not navigation.search(line)]
    insufficient=len(text)<300 or not prose
    return {'state':'insufficient_body' if insufficient else 'body_available_unverified',
            'reason_codes':(['empty_body'] if not text else ['short_body'] if len(text)<300 else ['navigation_or_fragment_only']) if insufficient else [],
            'character_count':len(content),'background_investigation_completed':False,
            'actual_compatibility':'unknown','purchase_intent':'unknown',
            'message':'正文不足，未完成背景调查' if insufficient else '已取得正文，尚未完成背景调查或客户身份核验',
            'next_action':'可手动选择其他公开页面；每次 Fetch 需重新确认，可能单独收费',
            'automatic_retry':False}
