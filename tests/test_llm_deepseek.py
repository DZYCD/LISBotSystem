# -*- coding: utf-8 -*-
"""DeepSeek LLM 连接测试脚本"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tll_protocol.llm import LLMConfig, LLMClient

def test_deepseek_llm():
    # DeepSeek API key 从环境变量读取（不入库）
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("FAIL: 未设置 DEEPSEEK_API_KEY 环境变量")
        return False
    config = LLMConfig(
        enabled=True,
        provider='openai',
        base_url='https://api.deepseek.com',
        api_key=api_key,
        model='deepseek-v4-pro',
        temperature=0.3,
        max_tokens=4096,
        role_prompt='你是测试助手。',
        style_prompt='请用中文简洁回答。',
    )
    client = LLMClient(config)
    if not client.is_ready:
        print('FAIL: LLM 客户端初始化失败，请检查 API Key 和网络')
        return False
    print('LLM 客户端初始化成功，开始调用 DeepSeek...')
    messages = [
        {'role': 'system', 'content': '你是测试助手。'},
        {'role': 'user', 'content': '请返回一个 JSON，内容为 {"reply":"测试成功","commands":[]}'}
    ]
    try:
        content = client.chat(messages)
        print('原始回复:', content)
        plan = client._extract_json(content)
        print('解析结果:', json.dumps(plan, ensure_ascii=False))
        if plan.get('reply'):
            print('测试通过')
            return True
        else:
            print('测试通过但缺少 reply 字段')
            return False
    except Exception as e:
        print('调用失败:', e)
        return False

if __name__ == '__main__':
    ok = test_deepseek_llm()
    sys.exit(0 if ok else 1)
