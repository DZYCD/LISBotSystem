import os
import requests
from bs4 import BeautifulSoup


def search(query, count=5):
    if not query:
        return "错误：请提供搜索关键词"
    count = min(int(count), 10)
    # Bocha 搜索 API key 从环境变量读取（不入库）
    api_key = os.environ.get("BOCHA_API_KEY", "")
    if not api_key:
        return "错误：未配置 BOCHA_API_KEY 环境变量"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {"query": query, "count": count, "summary": True}
    try:
        resp = requests.post("https://api.bochaai.com/v1/web-search", json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            return f"搜索失败，HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        results = data.get("data", {}).get("results", [])
        if not results:
            return f"未找到 '{query}' 的相关结果"
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("url", "")
            snippet = r.get("summary", r.get("snippet", "无摘要"))[:200]
            lines.append(f"{i}. {title}")
            lines.append(f"   链接: {url}")
            lines.append(f"   摘要: {snippet}")
        return "\n".join(lines)
    except requests.exceptions.Timeout:
        return "搜索超时，请稍后重试"
    except Exception as e:
        return f"搜索出错: {str(e)}"


def fetch_page(url, max_length=3000):
    if not url:
        return "错误：请提供有效的URL"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return f"访问失败，HTTP {resp.status_code}: {resp.reason}"
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title else "无标题"
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        text = "\n".join(lines)
        if len(text) > max_length:
            text = text[:max_length] + "\n\n...(以下内容已截断)"
        return f"标题: {title}\n来源: {url}\n\n{text}"
    except requests.exceptions.Timeout:
        return f"访问超时: {url}"
    except Exception as e:
        return f"抓取失败: {str(e)}"
