"""Non-sensitive explanations only. This module never accepts or repairs a URL."""
import ipaddress,re
from urllib.parse import urlsplit

def explain(value,error):
    code=error.code if error.code in ('website_url_invalid','website_unsafe_url') else 'url_policy_rejected'
    stage=getattr(error,'url_validation_stage','unknown')
    if stage not in ('deepseek_public_url','native_url_policy','page_url_policy'):stage='unknown'
    result={'reason_code':code,'validation_stage':stage,'raw_url_retained':False,'network_request_performed':False}
    from .webpage_url import REASONS
    policy_reason=getattr(error,'url_policy_reason',None)
    if policy_reason in REASONS:return dict(result,reason_detail=policy_reason)
    reason='policy_or_security_rejection_unspecified'
    if not isinstance(value,str):reason='url_not_text'
    elif not value or len(value)>8192:reason='missing_or_oversized_url'
    elif any(ord(c)<33 for c in value) or '\\' in value:reason='whitespace_control_or_backslash'
    else:
        try:
            u=urlsplit(value);host=(u.hostname or '').encode('idna').decode('ascii').lower();port=u.port
            if u.scheme not in ('http','https'):reason='non_http_scheme'
            elif not host:reason='missing_host'
            elif u.username is not None or u.password is not None:reason='embedded_credentials'
            elif port not in (None,80 if u.scheme=='http' else 443):reason='nonstandard_port'
            elif host in ('localhost','metadata','metadata.google.internal','instance-data') or host.endswith(('.localhost','.local','.internal')):reason='local_or_metadata_host'
            elif u.fragment:reason='fragment_not_supported'
            elif u.query:reason='query_not_supported_by_native_reader'
            elif len(value)>500:reason='native_reader_url_length_limit'
            else:
                try:
                    if not ipaddress.ip_address(host).is_global:reason='nonpublic_ip_literal'
                except ValueError:
                    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host:reason='invalid_hostname_shape'
                if any(part in ('.','..') for part in u.path.split('/')):reason='dot_path_segment'
                elif not u.path.isascii():reason='source_encoded_ascii_path_required'
        except (ValueError,UnicodeError):reason='malformed_url_or_port'
    return dict(result,reason_detail=reason)
