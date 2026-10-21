"""Server-owned snapshots, BM25, cached embeddings, RRF and bounded reranking.

The embedding and reranking providers must be local. This module has no network
client and does not generate text or modify the application knowledge database.
"""
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

SCHEMA_VERSION = 'structural-chunk-v1'
TOKENIZER_VERSION = 'zh-dictionary-bigram-v1'
WORDS = frozenset('夹紧 夹持 夹具 卡盘 液压 气动 回转 中空 通孔 安装 接口 主轴 工作台 精度 重复 定位 重复定位 加工 直径 行程 压力 转速 材料 不锈钢 铝合金 交期 价格 认证 有效 证书 专利 型号 规格 参数 维护 润滑 保养 质保 售后 工件 拉力 推力 允许 最大 最小 额定 扭矩'.split())
ALIASES = {'夹持':'夹紧', '保养':'维护', '通孔':'中空'}

def normalized(text):
    return unicodedata.normalize('NFKC', text).casefold().replace('μ', 'u').replace('µ', 'u')

def model_keys(text):
    # Whole identifiers, never substring matching (ABC-10 != ABC-100).
    return {re.sub(r'[-_]', '', x) for x in re.findall(r'(?<![a-z0-9])[a-z]+[-_]?\d+[a-z0-9_-]*(?![a-z0-9])', normalized(text))}

def equipment_model_keys(text):
    # Standard numbers are lexical evidence, not equipment identity constraints.
    return {key for key in model_keys(text) if not re.match(r'^(?:iso|din|iec|en)\d',key)}

def tokenize(text):
    value = normalized(text)
    result = re.findall(r'[a-z]+(?:[-_]?[a-z0-9]+)*|\d+(?:\.\d+)?', value)
    result.extend('model:' + x for x in sorted(model_keys(value)))
    for run in re.findall(r'[\u4e00-\u9fff]+', value):
        i = 0
        while i < len(run):
            word = next((run[i:i+n] for n in range(min(4, len(run)-i), 1, -1) if run[i:i+n] in WORDS), None)
            if word:
                result.append(ALIASES.get(word, word)); i += len(word)
            else:
                # Bigrams provide deterministic fallback, not claimed linguistic segmentation.
                result.append(run[i:i+2]); i += 1
        result.extend('zh2:' + run[j:j+2] for j in range(len(run)-1))
    return result

@dataclass(frozen=True)
class Chunk:
    document_id: str
    version: int
    chunk_id: str
    text: str
    location: Mapping[str, Any]
    heading_path: Tuple[str, ...] = ()
    kind: str = 'paragraph'
    models: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identity(self):
        return self.document_id, self.version, self.chunk_id

    @property
    def content_hash(self):
        return hashlib.sha256(self.embedding_text.encode('utf-8')).hexdigest()

    @property
    def embedding_text(self):
        return '\n'.join((*self.heading_path, self.text))

@dataclass(frozen=True)
class DocumentState:
    document_id: str
    version: int
    revision: int
    enabled: bool
    review_status: str
    title: str
    filename: str
    category: str
    source_company: str = ''
    expires_on: str = ''
    conflict: bool = False
    review_info: Optional[Mapping[str, Any]] = None
    review_label: str = ''

    def warnings(self):
        out = []
        if self.review_status == 'unverified': out.append('资料尚未核实')
        if self.expires_on and self.expires_on < dt.date.today().isoformat(): out.append('资料已过期')
        if self.conflict: out.append('资料可能冲突，需人工核对')
        if self.review_status == 'source_checked': out.append('仅核对原文，为企业自述；非独立证明')
        if self.review_status == 'confirmed': out.append('用户确认不等于独立有效证明')
        return out

def structure_chunks(document_id, version, parts, max_chars=900):
    """Parts already extracted/cleaned externally; table rows keep their headers.

    Never rekeys existing stored chunks. Call only while preparing a NEW version,
    review its import preview and let the existing store assign durable identities.
    Explicit oversize rows are marked for review rather than silently truncated.
    """
    if not 100 <= max_chars <= 4000: raise ValueError('max_chars out of range')
    output = []
    for part in parts:
        text = part['text'].strip()
        if not text: continue
        heading = tuple(part.get('heading_path', ()))
        kind = part.get('kind', 'paragraph')
        if kind == 'table_row':
            header = part.get('table_header', '')
            segments = [(header + '\n' + text).strip()]
        else:
            segments = []
            for paragraph in re.split(r'\n\s*\n', text):
                remaining = paragraph.strip()
                while remaining:
                    end = min(max_chars, len(remaining))
                    if end < len(remaining):
                        boundary = max(remaining.rfind(c, 0, end) for c in ('。','；','\n','. ','; '))
                        if boundary >= max_chars // 2: end = boundary + 1
                    segments.append(remaining[:end]); remaining = remaining[end:]
        for segment in segments:
            # Preparation-only ID, distinct from application-issued chunk IDs.
            ordinal = len(output)
            cid = hashlib.sha256(json.dumps([document_id, version, ordinal, segment], ensure_ascii=False).encode()).hexdigest()[:16]
            output.append(Chunk(document_id, version, cid, segment, dict(part['location']), heading, kind,
                tuple(sorted(model_keys(segment))), {'schema_version':SCHEMA_VERSION, 'preparation_only':True,
                'oversize_requires_review':len(segment)>max_chars, 'ordinal':ordinal}))
    return output

class Embedder(Protocol):
    model_id: str
    model_version: str
    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
    def embed_query(self, text: str) -> Optional[Sequence[float]]: ...

class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]: ...

def unit_vector(vector):
    value = [float(x) for x in vector]
    if not value or any(not math.isfinite(x) for x in value): raise ValueError('invalid vector')
    length = math.sqrt(sum(x*x for x in value))
    if not length: raise ValueError('zero vector')
    return [x/length for x in value]

class VectorCache:
    """Separate prototype DB. Content/model/revision/schema-aware keys; no status authority."""
    def __init__(self, path):
        self.path = Path(path)
        if self.path.name != 'rag_vectors.sqlite3': raise ValueError('use dedicated rag_vectors.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, value TEXT NOT NULL)')

    def key(self, chunk, provider):
        data = [*chunk.identity, chunk.content_hash, provider.model_id, provider.model_version, SCHEMA_VERSION]
        return hashlib.sha256(json.dumps(data).encode()).hexdigest()

    def vectors(self, chunks, provider):
        keys = [self.key(c, provider) for c in chunks]
        results = [None] * len(keys)
        with sqlite3.connect(str(self.path)) as db:
            for i, key in enumerate(keys):
                row = db.execute('SELECT value FROM vectors WHERE cache_key=?', (key,)).fetchone()
                if row: results[i] = unit_vector(json.loads(row[0]))
            missing = [i for i, value in enumerate(results) if value is None]
            if missing:
                vectors = list(provider.embed_documents([chunks[i].embedding_text for i in missing]))
                if len(vectors) != len(missing): raise ValueError('embedding count mismatch')
                for i, value in zip(missing, vectors):
                    results[i] = unit_vector(value)
                    db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)', (keys[i],json.dumps(results[i])))
            # Validate the whole batch before SQLite commits. Failure rolls back new rows.
            if len({len(x) for x in results}) > 1: raise ValueError('embedding dimensions mismatch')
        return results

def bm25(query, texts):
    counters = [Counter(tokenize(text)) for text in texts]
    lengths = [sum(c.values()) for c in counters]
    average = sum(lengths)/max(1,len(lengths)) or 1
    scores = [0.0] * len(texts)
    for term in set(tokenize(query)):
        count = sum(term in counter for counter in counters)
        if not count: continue
        idf = math.log(1 + (len(texts)-count+0.5)/(count+0.5))
        for i, counter in enumerate(counters):
            freq = counter[term]
            scores[i] += idf * freq*2.2/(freq + 1.2*(0.25+0.75*lengths[i]/average))
    return scores

def rrf(rankings, constant=60):
    scores = Counter()
    for ranking in rankings:
        for rank, identity in enumerate(dict.fromkeys(ranking), 1): scores[identity] += 1/(constant+rank)
    return sorted(scores, key=lambda key:(-scores[key],key)), dict(scores)

class HybridRetriever:
    def __init__(self, snapshot, embedder=None, cache=None, reranker=None, min_cosine=0.75,
            include_conflicts=True, include_expired=True, min_rerank_score=None):
        """snapshot() returns fresh server-owned (DocumentState list, Chunk list)."""
        if embedder and not cache: raise ValueError('embedding requires dedicated cache')
        if not -1 <= min_cosine <= 1: raise ValueError('invalid cosine threshold')
        self.snapshot = snapshot; self.embedder = embedder; self.cache = cache
        self.reranker = reranker; self.min_cosine = min_cosine
        # Server configuration, never request parameters that can bypass governance.
        self.include_conflicts = include_conflicts; self.include_expired = include_expired
        if min_rerank_score is not None and not math.isfinite(float(min_rerank_score)):raise ValueError('invalid rerank gate')
        if min_rerank_score is not None and not reranker:raise ValueError('rerank gate requires actual reranker')
        self.min_rerank_score = float(min_rerank_score) if min_rerank_score is not None else None

    def eligible(self, chunk, docs, category, review_status):
        d = docs.get(chunk.document_id)
        return bool(d and chunk.version == d.version and d.enabled and
            d.review_status in ('unverified','source_checked','confirmed') and
            (self.include_conflicts or not d.conflict) and
            (self.include_expired or not (d.expires_on and d.expires_on < dt.date.today().isoformat())) and
            (not category or category == d.category) and (not review_status or review_status == d.review_status))

    def search(self, query, top_k=6, candidate_k=20, category='', review_status=''):
        if not isinstance(query,str) or not query.strip() or len(query)>200: raise ValueError('query must be 1..200 chars')
        if type(top_k) is not int or type(candidate_k) is not int or not 1<=top_k<=20 or not top_k<=candidate_k<=100: raise ValueError('invalid top K')
        if category and category not in ('product','capability','certification','case','sales_rule'): raise ValueError('invalid category')
        if review_status and review_status not in ('unverified','source_checked','confirmed','rejected'): raise ValueError('invalid review status')
        states, chunks = self.snapshot(); docs = {d.document_id:d for d in states}
        pool = sorted((c for c in chunks if self.eligible(c,docs,category,review_status)), key=lambda c:c.identity)
        identities = [c.identity for c in pool]
        if len(set(identities)) != len(identities): raise ValueError('duplicate chunk identity')
        asked_models = equipment_model_keys(query)
        if asked_models:
            # A brochure title must not make a different model's row eligible.
            def chunk_models(c):
                explicit = {normalized(x).replace('-','').replace('_','') for x in c.models}
                return explicit or model_keys(c.text) or model_keys(docs[c.document_id].title)
            pool = [c for c in pool if asked_models & chunk_models(c)]
        if not pool: return self.response([], 'no_eligible_evidence')
        texts = [docs[c.document_id].title+'\n'+c.embedding_text for c in pool]
        lexical = bm25(query,texts)
        lex_order = sorted((i for i in range(len(pool)) if lexical[i]>0), key=lambda i:(-lexical[i],pool[i].identity))[:candidate_k]
        rankings = [[pool[i].identity for i in lex_order]]
        similarities = {}
        if self.embedder:
            raw_query = self.embedder.embed_query(query)
            # A local provider may explicitly abstain. Never force an unknown query
            # into a synthetic zero/unknown vector that matches unknown documents.
            if raw_query is not None:
                vectors = self.cache.vectors(pool,self.embedder); q = unit_vector(raw_query)
                if any(len(v)!=len(q) for v in vectors): raise ValueError('query dimensions mismatch')
                similarities = {c.identity:sum(a*b for a,b in zip(q,v)) for c,v in zip(pool,vectors)}
                vec_order = sorted((c.identity for c in pool if similarities[c.identity]>=self.min_cosine), key=lambda key:(-similarities[key],key))[:candidate_k]
                rankings.append(vec_order)
        fused, scores = rrf(rankings)
        by_id = {c.identity:c for c in pool}; fused = fused[:candidate_k]
        rerank_scores = {}
        if self.reranker and fused:
            values = [float(x) for x in self.reranker.score(query,[by_id[key].embedding_text for key in fused])]
            if len(values)!=len(fused) or any(not math.isfinite(x) for x in values): raise ValueError('invalid reranking scores')
            rerank_scores = dict(zip(fused,values))
            fused.sort(key=lambda key:(-rerank_scores[key],-scores[key],key))
            if self.min_rerank_score is not None:
                fused=[key for key in fused if rerank_scores[key]>=self.min_rerank_score]
        # Re-read canonical state/content AFTER providers: stale indexes cannot revive disabled/old documents.
        states, fresh_chunks = self.snapshot(); fresh_docs = {d.document_id:d for d in states}
        fresh = {c.identity:c for c in fresh_chunks}; results = []
        for key in fused:
            c = fresh.get(key)
            if not c or c != by_id[key] or fresh_docs.get(c.document_id)!=docs.get(c.document_id) or not self.eligible(c,fresh_docs,category,review_status): continue
            d = fresh_docs[c.document_id]
            results.append(dict(document_id=c.document_id,version=c.version,chunk_id=c.chunk_id,text=c.text,
                location=dict(c.location),title=d.title,filename=d.filename,category=d.category,source_company=d.source_company,
                review_status=d.review_status,review_label=d.review_label or {'unverified':'待核实','source_checked':'已核对原文（企业自述）','confirmed':'用户确认（非独立证明）'}.get(d.review_status,''),review_info=d.review_info,revision=d.revision,independently_proven=False,
                expired=bool(d.expires_on and d.expires_on<dt.date.today().isoformat()),conflict=d.conflict,
                warnings=d.warnings(),score=scores[key],lexical_score=lexical[pool.index(by_id[key])],
                vector_score=similarities.get(key),rerank_score=rerank_scores.get(key)))
        response = self.response(results[:top_k], 'candidate_evidence' if results else 'no_eligible_evidence')
        response['total'] = len(results)
        return response

    def response(self, items, status):
        return {'method':'hybrid_rrf' if self.embedder else 'local_bm25','items':items,'total':len(items),
            'status':status,'requires_preview':True,'cloud_context_added':False,
            'embedding_model':self.embedder.model_id if self.embedder else None,
            'reranking_enabled':bool(self.reranker),
            'notice':'检索候选不等于事实；未知、冲突和过期资料需核实。须手动选择并完成现有发送预览。'}

    @staticmethod
    def selections(response):
        """Existing selection protocol unchanged; caller must explicitly pick <=6."""
        if len(response['items'])>6: raise ValueError('select at most six chunks')
        return [{k:item[k] for k in ('document_id','version','chunk_id')} for item in response['items']]
