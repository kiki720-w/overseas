"""Read-only source-material projection. Never changes provenance or index state."""
import collections
import hashlib
import json
import re
from .common import AppError, field, paging, page_result

# Verified local cleaning evidence, not an upload job or a PDF stored in kb_versions.
SOURCE_CATALOG = {}  # Private legacy document mappings are intentionally omitted.
# Exact legacy version/digest allowlist from the frozen, independently checked KB snapshot.
LEGACY_SOURCE_RECORDS = {}  # Private legacy document mappings are intentionally omitted.
HEX = re.compile(r'^[a-f0-9]{64}$')


def legacy_header(text):
    """Only inspect a bounded leading structured provenance object, never free-text guesses."""
    if not isinstance(text, str): return {}
    prefix = text[:16000]
    start = prefix.find('{')
    if start < 0 or start > 500: return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(prefix[start:])
    except ValueError: return {}
    return value if isinstance(value, dict) else {}


def provenance(content, document):
    metadata = content.get('metadata') or {}
    key = str(document.get('id')) + ':' + str(content['version']) + ':' + str(content.get('sha256'))
    header = legacy_header(content.get('extracted_text', '')) if LEGACY_SOURCE_RECORDS.get(key) else {}
    if header.get('source_sha256') != LEGACY_SOURCE_RECORDS.get(key): header = {}
    # Current review belongs to current version only. Do not attach it to historical versions.
    review = (document.get('review_info') or {}) if content['version'] == document['version'] else {}
    source = review.get('source') or {}
    candidates = [
        (metadata.get('source_pdf_sha256'), source.get('filename'), 'version_metadata'),
        (header.get('source_sha256'), header.get('source_filename'), 'legacy_structured_provenance'),
        (source.get('sha256'), source.get('filename'), 'current_review_source'),
    ]
    for digest, filename, basis in candidates:
        if isinstance(digest, str) and HEX.fullmatch(digest):
            return digest, SOURCE_CATALOG.get(digest, {}).get('filename') or filename or '来源文件名未记录', basis
    digest = content.get('sha256')
    if isinstance(digest, str) and HEX.fullmatch(digest):
        return digest, content.get('filename') or document.get('filename') or '未命名资料', 'stored_content_sha256'
    return None, content.get('filename') or document.get('filename') or '未命名资料', 'unlinked_document_version'


class KnowledgeSources:
    def __init__(self, store): self.store = store

    def groups(self):
        groups = {}
        with self.store.db() as db:
            documents = {did: dict(json.loads(raw), id=did) for did, raw in db.execute('SELECT id,data FROM kb_documents')}
            versions = db.execute('SELECT document_id,version,data,length(original) FROM kb_versions ORDER BY rowid').fetchall()
            counts = {(did, version): n for did, version, n in db.execute('SELECT document_id,version,count(*) FROM kb_chunks GROUP BY document_id,version')}
        for did, version, raw, original_size in versions:
            document = documents.get(did)
            if not document: continue  # Orphans are not silently attached to a different document.
            content = dict(json.loads(raw), version=version)
            digest, filename, basis = provenance(content, document)
            # Native uploads retain same-filename versions in the same document lineage;
            # never merge unrelated same-name uploads or changed filenames by guessing.
            sid = hashlib.sha256(('stored:' + did + ':' + filename).encode()).hexdigest() if basis == 'stored_content_sha256' else digest or hashlib.sha256(('unlinked:' + did + ':' + str(version)).encode()).hexdigest()
            g = groups.setdefault(sid, {'source_id': sid, 'sha256': digest, 'filename': filename,
                'page_count': SOURCE_CATALOG.get(digest, {}).get('page_count'),
                'page_count_basis': SOURCE_CATALOG.get(digest, {}).get('page_count_basis'),
                'versions': [], 'stored_originals': [], 'source_companies': set(), 'provenance_bases': set()})
            is_current = version == document['version']
            chunk_count = counts.get((did, version), 0)
            searchable = bool(is_current and document.get('enabled') and document.get('review_status') != 'rejected' and chunk_count)
            manual = content.get('parser') == 'cleaned-v2-structured-candidate' or (digest != content.get('sha256') and basis != 'unlinked_document_version')
            entry = {'document_id': did, 'version': version, 'title': content.get('title') or document.get('title', ''),
                'filename': content.get('filename', ''), 'is_current': is_current, 'category': content.get('category') or document.get('category'),
                'created_at': content.get('created_at'), 'chunk_count': chunk_count,
                'extracted_chars': len(content.get('extracted_text') or ''), 'parser': content.get('parser'),
                'parse_state': 'manual_prepared' if manual else ('extracted' if content.get('extracted_text') else 'no_text'),
                'review_status': document.get('review_status') if is_current else None,
                'review_scope': (document.get('review_info') or {}).get('scope_notice') if is_current else None,
                'enabled': document.get('enabled') if is_current else None, 'searchable': searchable,
                'revision': document.get('revision') if is_current else None, 'source_link_basis': basis,
                'content_sha256': content.get('sha256'), 'source_sha256': digest,
                'stored_original_bytes': original_size or 0}
            g['versions'].append(entry)
            g['provenance_bases'].add(basis)
            if content.get('source_company'): g['source_companies'].add(content['source_company'])
            # The logical text BLOB is not the referenced brochure PDF.
            if digest and content.get('sha256') == digest and original_size:
                g['stored_originals'].append({'document_id': did, 'version': version, 'filename': content.get('filename', ''),
                    'url': '/api/knowledge/original?id=' + did + '&version=' + str(version)})
        # Missing current versions remain visible as unresolved entries, not success states.
        represented = {(x['document_id'], x['version']) for g in groups.values() for x in g['versions']}
        for did, d in documents.items():
            if (did, d['version']) not in represented:
                sid = hashlib.sha256(('missing:' + did).encode()).hexdigest()
                groups[sid] = {'source_id': sid, 'sha256': None, 'filename': d.get('filename', '来源未关联'), 'page_count': None,
                    'page_count_basis': None, 'versions': [{'document_id': did, 'version': d['version'], 'title': d.get('title', ''),
                        'filename': d.get('filename', ''), 'is_current': True, 'category': d.get('category'), 'created_at': None,
                        'chunk_count': 0, 'extracted_chars': 0, 'parser': None, 'parse_state': 'missing_version',
                        'review_status': d.get('review_status'), 'enabled': d.get('enabled'), 'searchable': False,
                        'revision': d.get('revision'), 'review_scope': None, 'source_link_basis': 'missing_version', 'stored_original_bytes': 0}],
                    'stored_originals': [], 'source_companies': set(), 'provenance_bases': {'missing_version'}}
        return [self.summary(g) for g in groups.values()]

    def summary(self, group):
        g = dict(group)
        versions = g['versions']; current = [v for v in versions if v['is_current']]
        source_hashes = sorted({v.get('source_sha256') for v in versions if v.get('source_sha256')})
        g['source_hashes'] = source_hashes
        g['sha256'] = source_hashes[0] if len(source_hashes) == 1 else None
        g.update(logical_document_count=len({v['document_id'] for v in versions}), current_document_count=len(current),
            searchable_document_count=sum(v['searchable'] for v in current),
            searchable_chunk_count=sum(v['chunk_count'] for v in current if v['searchable']),
            current_chunk_count=sum(v['chunk_count'] for v in current), history_version_count=sum(not v['is_current'] for v in versions),
            archived_current_document_count=sum(not v['searchable'] for v in current),
            review_status_counts=dict(collections.Counter(v['review_status'] for v in current)),
            first_saved_at=min((v['created_at'] for v in versions if v['created_at']), default=None),
            last_saved_at=max((v['created_at'] for v in versions if v['created_at']), default=None),
            uploaded_at=None, upload_state='stored_original' if g['stored_originals'] else 'referenced_source',
            original_available=bool(g['stored_originals']), source_companies=sorted(g['source_companies']),
            provenance_bases=sorted(g['provenance_bases']),
            parse_state='manual_prepared' if any(v['parse_state'] == 'manual_prepared' for v in current) else
                ('extracted' if any(v['extracted_chars'] for v in current) else 'no_current_text'),
            retrieval_state='local_keyword_ready' if any(v['searchable'] for v in current) else 'not_searchable',
            vector_index_state='not_reported', automatic_pipeline=False, historical_parse_failures_persisted=False,
            failure_reason=None, independently_proven=False)
        if any(v['parse_state'] == 'missing_version' for v in current):
            g['failure_reason'] = '当前版本记录缺失；无法确认解析或可检索状态。'
        elif current and not any(v['extracted_chars'] for v in current):
            g['failure_reason'] = '当前版本没有可读取的文本；未保存解析任务失败原因。'
        return g

    def listing(self, query):
        p, size = paging(query); term = field(query, 'q', 200).casefold(); state = field(query, 'state', 40)
        if state not in ('', 'local_keyword_ready', 'not_searchable'): raise AppError('来源状态筛选无效')
        groups = self.groups()
        items = [g for g in groups if (not term or term in (g['filename'] + ' ' + ' '.join(g['source_companies'])).casefold()) and (not state or g['retrieval_state'] == state)]
        items.sort(key=lambda g: (g['last_saved_at'] or '', g['source_id']), reverse=True)
        summaries = [{k: v for k, v in g.items() if k != 'versions'} for g in items]
        return dict(page_result(summaries[(p-1)*size:p*size], len(items), p, size), schema='knowledge-sources-v1',
            capabilities={'automatic_pipeline': False, 'ocr': False, 'failure_history': False, 'source_write': False},
            totals={'source_count': len(groups), 'logical_document_count': sum(g['current_document_count'] for g in groups),
                'searchable_chunk_count': sum(g['searchable_chunk_count'] for g in groups)})

    def detail(self, source_id):
        if not isinstance(source_id, str) or not HEX.fullmatch(source_id): raise AppError('来源标识无效')
        group = next((g for g in self.groups() if g['source_id'] == source_id), None)
        if group is None: raise AppError('来源资料不存在', 404, 'not_found')
        return dict(group, schema='knowledge-sources-v1')

    def review_source(self, document_id, version=None):
        detail = self.store.knowledge_document(document_id, version)
        content = detail['content']; digest, filename, basis = provenance(content, detail)
        source = (detail.get('review_info') or {}).get('source') or {}
        header = legacy_header(content.get('extracted_text', '')) if basis == 'legacy_structured_provenance' else {}
        pages = source.get('pdf_pages') if detail['selected_version'] == detail['version'] else None
        if not pages: pages = header.get('pdf_pages')
        if not pages:
            pages = []
            for chunk in detail.get('chunks', []):
                location = chunk.get('location') or {}
                pages.extend(location.get('pdf_pages') or [])
                if type(location.get('page')) is int: pages.append(location['page'])
        pages = sorted({p for p in (pages or []) if type(p) is int and p > 0})
        printed = source.get('printed_pages') or header.get('printed_pages') or []
        # A native upload has reliable own-file name/hash too. Only its unknown PDF
        # page references remain missing; never turn paragraph numbers into PDF pages.
        linked = basis != 'unlinked_document_version' and bool(digest)
        return {'document_id': document_id, 'version': detail['selected_version'], 'basis': basis,
            'filename': filename if linked else None, 'sha256': digest if linked else None,
            'pdf_pages': pages, 'printed_pages': [p for p in printed if isinstance(p, str)],
            'missing': [k for k, v in {'filename': filename if linked else None, 'sha256': digest if linked else None, 'pdf_pages': pages}.items() if not v],
            'notice': '自动关联已记录的来源；这是溯源信息，不是新增核实或扩大审核范围。'}
