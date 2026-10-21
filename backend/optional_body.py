# -*- coding: utf-8 -*-
"""Optional extraction seam: reuse validated native paragraphs; never replace safe fetching."""
import re,hashlib
from pathlib import Path
from .website_text import evidence_lines
PRODUCT=re.compile(r'cnc|machin\w*|gears?|fixtures?|swiss|turn\w*|mill\w*|产品|加工|工艺|夹具|设备',re.I)
ASSERTION=re.compile(r'\b(?:we|our|offers?|provides?|manufactur\w*|produc\w*|specializ\w*|has|have|using|uses|including|scheduled|capabilit\w*)\b|生产|提供|加工|用于',re.I)
def norm(text):return ' '.join(text.split())
def body_sentence(text):return len(text)>=45 and (len(text.split())>=8 or len(re.findall(r'[\u4e00-\u9fff]',text))>=16) and bool(ASSERTION.search(text))
def extract_optional(raw,text,parser,url,fetched_at):
 from lxml import html
 from .trafilatura_adapter import extract_saved_html
 native_lines,_,native_method=evidence_lines(parser);visible={norm(s) for s in native_lines};tree=html.fromstring(text);order={n:i for i,n in enumerate(tree.iter())};candidates=[]
 for node in tree.xpath('//p|//td'):
  full=norm(node.text_content())
  if full not in visible or not body_sentence(full):continue
  family=[node,*node.iterancestors()]
  if any(n.tag in parser.BLOCK or n.get('role','').lower() in ('navigation','banner','contentinfo') or 'hidden' in n.attrib or n.get('aria-hidden','').lower()=='true' or re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',n.get('style',''),re.I) for n in family):continue
  if sum(len(norm(a.text_content())) for a in node.xpath('.//a'))>len(full)*.5:continue
  heading=None
  for parent in list(node.iterancestors())[:4]:
   found=[h for h in parent.xpath('./h1|./h2|./h3|./h4') if order[h]<order[node] and PRODUCT.search(norm(h.text_content()))]
   if found:heading=norm(found[-1].text_content())[:200];break
  candidates.append({'text':full,'heading':heading,'selection_features':{'paragraph_tag':node.tag,'normalized_characters':len(full),'word_count':len(full.split()),'assertion_match':ASSERTION.search(full).group(0),'product_terms':sorted({m.group(0).lower() for m in PRODUCT.finditer(full)}),'preceding_product_heading':heading,'anchor_character_ratio':round(sum(len(norm(a.text_content())) for a in node.xpath('.//a'))/len(full),4),'native_paragraph_alignment':True},'position':{'xpath':tree.getroottree().getpath(node),'html_line':node.sourceline,'tag':node.tag},'responsive_visibility_unverified':any('hidden-' in n.get('class','') for n in family)})
 primary=extract_saved_html(raw,url,fetched_at,'balanced');primary_text=norm(primary.get('text') or '');selected=[];seen=set()
 def add(c,extractor,fallback,reason):
  quote=c['text'][:300]
  if quote in seen:return
  seen.add(quote);selected.append(dict(c,quote=quote,extractor=extractor,extractor_version=primary.get('extractor_version') if not fallback else hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),native_parser_version=hashlib.sha256(Path(__file__).with_name('website_text.py').read_bytes()).hexdigest(),source_url=url,fetched_at=fetched_at,source_html_sha256=hashlib.sha256(raw).hexdigest(),fallback=fallback,fallback_reason=reason,confidence='low_trust_structured_product_requires_human_review' if fallback else 'source_aligned_website_self_statement_unverified',source_alignment='exact_normalized_native_visible_paragraph',identity_verified=False,actual_compatibility='unknown',purchase_intent='unknown'))
 primary_candidates=[c for c in candidates if c['text'] in primary_text]
 fallback_candidates=[c for c in candidates if c['text'] not in primary_text and c['heading'] and PRODUCT.search(c['text'])]
 # Bounded complement, independent of hostname. Each added paragraph was missing in the primary output.
 fallback_candidates.sort(key=lambda c:len(set(x.group(0).lower() for x in PRODUCT.finditer(c['text']))),reverse=True)
 chosen_fallback=fallback_candidates[:3]
 for c in primary_candidates[:8-len(chosen_fallback)]:add(c,'trafilatura',False,None)
 for c in chosen_fallback:add(c,'structured_product_fallback',True,'paragraph_missing_in_primary' if primary_text else 'primary_missing_or_error')
 from .html_links import candidates as link_candidates
 links=link_candidates(tree,text,url,hashlib.sha256(raw).hexdigest())
 return {'link_discovery':links,'feature_enabled':True,'primary_status':primary['status'],'primary_extractor':'trafilatura','primary_version':primary.get('extractor_version'),'primary_internal_fallback':primary.get('internal_backup_extraction'),'status':'structured_product_fallback_only_low_trust' if selected and all(x['fallback'] for x in selected) else 'primary_with_limited_structured_fallback' if selected and any(x['fallback'] for x in selected) else 'source_aligned_primary' if selected else 'body_insufficient','fallback_count':sum(s['fallback'] for s in selected),'fallback_limit':3,'total_quote_limit':8,'quote_character_limit':300,'candidate_paragraphs':len(candidates),'unquoted_candidate_count':max(0,len(candidates)-len(selected)),'native_method':native_method,'segments':selected,'company_identity_verified':False,'actual_compatibility':'unknown','purchase_intent':'unknown','scope_notice':'static HTML paragraph provenance; optional extractor and low-trust supplements do not establish company identity, compatibility or demand'}
