import json,unittest
from backend.common import AppError
from backend.url_rejection_diagnostics import explain
from backend.deepseek_tools import DeepSeekTools
from test_deepseek_tools import row

class URLDiagnosticPrivacyTests(unittest.TestCase):
    def test_only_fixed_categories_no_credential_token_or_full_url(self):
        values=['https://user-sentinel:password-sentinel@example.com/x?token=query-sentinel#fragment-sentinel',
                'https://example.com/path?api_key=key-sentinel&signature=signature-sentinel',
                'http://127.0.0.1/private?access_token=access-sentinel',
                'https://user%40sentinel:pass%3Asentinel@example.com/path',
                'https://example.com:port-sentinel/']
        for value in values:
            error=AppError(value,400,'website_url_invalid');error.url_validation_stage='native_url_policy'
            result=explain(value,error);text=json.dumps(result)
            self.assertEqual(set(result),{'reason_code','validation_stage','raw_url_retained','network_request_performed','reason_detail'})
            self.assertFalse(result['raw_url_retained']);self.assertFalse(result['network_request_performed'])
            for secret in ('sentinel','example.com','127.0.0.1','https://','http://'):self.assertNotIn(secret,text)
    def test_unexpected_error_code_and_stage_are_not_echoed(self):
        error=AppError('unused',400,'https://u:secret-sentinel@internal/');error.url_validation_stage='token=stage-sentinel'
        result=explain('https://example.com/?token=query-sentinel',error)
        self.assertEqual(result['reason_code'],'url_policy_rejected');self.assertEqual(result['validation_stage'],'unknown');self.assertNotIn('sentinel',json.dumps(result))
    def test_provider_response_removes_all_rejected_sensitive_urls(self):
        urls=['https://user-sentinel:password-sentinel@example.com/', 'https://example.com/path?token=query-sentinel', 'http://127.0.0.1/private#fragment-sentinel']
        provider=DeepSeekTools(key='synthetic-unused')
        body={'results':[row(url) for url in urls]};provider.validate_response('search',body,5)
        text=json.dumps(body);self.assertEqual(body['results'],[]);self.assertEqual(len(body['result_filtering']['url_rejection_diagnostics']),3)
        for secret in ('sentinel','127.0.0.1','example.com','https://'):self.assertNotIn(secret,text)
