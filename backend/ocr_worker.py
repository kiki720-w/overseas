"""Optional offline Docling/PDFium worker. No server or credential imports."""
import sys,json,pathlib,hashlib,os
request=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf8'))
root=pathlib.Path(sys.argv[1]).resolve().parent
def deny(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.bind'):raise PermissionError('local OCR network disabled')
sys.addaudithook(deny)
try:
    import torch,pypdfium2
    from docling.document_converter import DocumentConverter,PdfFormatOption
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions,RapidOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions,AcceleratorDevice
    source=root/'source.pdf'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==request['source_sha256']
    pdf=pypdfium2.PdfDocument(str(source));page_count=len(pdf);pdf.close()
    page=request['pages'][0]
    if not 1<=page<=page_count:raise ValueError('selected page is outside the PDF')
    torch.set_num_threads(2)
    options=PdfPipelineOptions(artifacts_path=pathlib.Path(request['models']),enable_remote_services=False,
      do_ocr=True,do_table_structure=True,do_code_enrichment=False,do_formula_enrichment=False,
      do_picture_description=False,do_picture_classification=False,
      accelerator_options=AcceleratorOptions(num_threads=2,device=AcceleratorDevice.CPU))
    options.ocr_options=RapidOcrOptions(lang=['ch'],backend='onnxruntime',force_full_page_ocr=True,
      rapidocr_params={'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1})
    converter=DocumentConverter(allowed_formats=[InputFormat.PDF],format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options,backend=PyPdfiumDocumentBackend)})
    result=converter.convert(source,page_range=(page,page))
    if str(result.status)!='ConversionStatus.SUCCESS':raise ValueError('page conversion did not complete')
    raw=result.document.export_to_dict();raw_bytes=json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()
    (root/'document.json').write_bytes(raw_bytes)
    artifact_hash=hashlib.sha256(raw_bytes).hexdigest();size=raw['pages'][str(page)]['size']
    def box(b):
        if b is None:return None
        return b if b['coord_origin']=='TOPLEFT' else dict(l=b['l'],r=b['r'],t=size['height']-b['t'],b=size['height']-b['b'],coord_origin='TOPLEFT')
    def identity(kind,index):return hashlib.sha256(f"{request['source_sha256']}|{artifact_hash}|docling-pdfium|{kind}|{index}".encode()).hexdigest()
    draft=dict(source_sha256=request['source_sha256'],source_page_count=page_count,pdf_page=page,page_size_pt=size,engine='docling-pdfium',engine_version='2.133.0',backend='PyPdfiumDocumentBackend',raw_artifact_sha256=artifact_hash,
      processing={'ocr_performed':True,'requested_pages':[page],'scope':'selected_pages_only'},review_state='unverified',cloud_context_approved=False,text_blocks=[],tables=[],
      warnings=['OCR可能漏文、错列或合并独立表；缺失格不等于源文空白，所有参数仍待审','Markdown不作为原文证据；未解析页不视为完成'])
    for i,text in enumerate(raw['texts']):draft['text_blocks'].append(dict(id=identity('text',i),text_raw=text['text'],provenance_raw=text['prov'],requires_review=True))
    for ti,table in enumerate(raw['tables']):
        cells=[];observed=set()
        for ci,cell in enumerate(table['data']['table_cells']):
            for r in range(cell['start_row_offset_idx'],cell['end_row_offset_idx']):
                for c in range(cell['start_col_offset_idx'],cell['end_col_offset_idx']):observed.add((r,c))
            cells.append(dict(id=identity('cell',f'{ti}:{ci}'),text_raw=cell['text'],row=cell['start_row_offset_idx'],col=cell['start_col_offset_idx'],row_span=cell['row_span'],col_span=cell['col_span'],bbox_pt=box(cell.get('bbox')),provenance_raw=cell,unit=None,model_binding=None,requires_review=True))
        missing=[dict(row=r,col=c,state='unobserved') for r in range(table['data']['num_rows']) for c in range(table['data']['num_cols']) if (r,c) not in observed]
        draft['tables'].append(dict(id=identity('table',ti),cells=cells,num_rows=table['data']['num_rows'],num_cols=table['data']['num_cols'],provenance_raw=table['prov'],uncovered_grid_positions=missing))
    if len(raw_bytes)>16*1024*1024:raise ValueError('output limit')
    (root/'result.json').write_text(json.dumps(dict(ok=True,draft=draft),ensure_ascii=False),encoding='utf8')
except Exception as error:
    code='component_missing' if isinstance(error,ImportError) else 'parser_failed'
    (root/'result.json').write_text(json.dumps(dict(ok=False,code=code,message='缺少本地Docling/PDFium组件' if code=='component_missing' else '本地页面解析失败或页码不支持；未入库')),encoding='utf8')
    sys.exit(2)
