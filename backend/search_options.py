"""Public Search options shared by planning and the explicit search boundary."""
from .common import AppError

DEFAULT_COUNT = 20


def deepseek_options(data, original):
    count = data.get('count', DEFAULT_COUNT)
    content = data.get('need_content', False)
    question = data.get('ori_question', original)
    if type(count) is not int or not 1 <= count <= 50 or type(content) is not bool:
        raise AppError('DeepSeek 候选数量需为 1–50 的整数，正文选项需为布尔值', 400, 'invalid_search_options')
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 128:
        raise AppError('原始公开需求需为 1–128 字；不会自动附加工厂私密资料或文档', 400, 'invalid_original_question')
    return {'count': count, 'need_content': content, 'ori_question': question}
