# -*- coding: utf-8 -*-
"""Offline HTML-only adapter. Network safety remains the caller's existing fetch boundary."""
import hashlib,importlib.metadata,importlib.util
def extract_saved_html(raw,url,fetched_at,mode='balanced'):
 if not isinstance(raw,bytes):raise TypeError('raw saved HTML bytes required')
 if len(raw)>256*1024:return {'status':'input_too_large','text':None,'fallback_used':False}
 if mode not in ('balanced','precision','fast','recall'):raise ValueError('Invalid explicit extraction mode')
 result={'raw_html_sha256':hashlib.sha256(raw).hexdigest(),'url':url,'fetched_at':fetched_at,'extractor':'trafilatura','extractor_version':None,'mode':mode,'fallback_used':None,'external_fallback_used':False,'external_requests':0,'identity_verified':False,'actual_compatibility':'unknown','purchase_intent':'unknown'}
 if importlib.util.find_spec('trafilatura') is None:return dict(result,status='dependency_missing',text=None,reason='trafilatura is not installed; no substitute extractor executed')
 result['extractor_version']=importlib.metadata.version('trafilatura')
 # Only extract is used. No fetch_url, discovery/crawler, remote resource or model API.
 from trafilatura import extract
 options=dict(url=url,output_format='txt',include_comments=False,include_tables=True,include_links=False,include_images=False,favor_precision=mode=='precision',favor_recall=mode=='recall')
 # Balanced/precision retain the official default backup path; actual internal selection is not exposed.
 options['fast']=mode=='fast';result['options']=options;result['internal_backup_extraction']='skipped_by_fast_true' if mode=='fast' else 'library_default_possible_not_observed';result['fallback_used']=False if mode=='fast' else None
 try:text=extract(raw,**options)
 except Exception as exc:return dict(result,status='extractor_error',text=None,error_type=type(exc).__name__)
 if not text or not text.strip():return dict(result,status='body_missing',text=None,reason='Extractor returned no substantive output; not success; no local fallback applied')
 return dict(result,status='extracted_text_requires_source_check',text=text)
