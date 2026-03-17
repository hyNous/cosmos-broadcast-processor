"""
小宇宙播客音频提取器
从 episode 页面的 meta 标签中提取音频 URL 和元数据
"""
import requests
from bs4 import BeautifulSoup


class XiaoYuZhouExtractor:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.xiaoyuzhoufm.com/",
    }

    def extract(self, url: str) -> dict:
        """
        从小宇宙 episode 页面提取音频信息

        返回:
            {
                "audio_url": str,
                "title": str,
                "cover": str | None,
                "description": str | None,
            }
        异常:
            ValueError: 页面无 og:audio 或 URL 无效
            requests.RequestException: 网络错误
        """
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            raise ConnectionError("请求超时，请检查网络连接")
        except requests.exceptions.ConnectionError:
            raise ConnectionError("无法连接到小宇宙，请检查网络连接")
        except requests.exceptions.HTTPError as e:
            raise ValueError(f"页面请求失败：{e}")

        soup = BeautifulSoup(resp.text, "html.parser")

        def get_meta(property_name: str) -> str | None:
            tag = soup.find("meta", property=property_name)
            if tag:
                return tag.get("content", "").strip() or None
            return None

        audio_url = get_meta("og:audio")
        if not audio_url:
            raise ValueError("未找到音频链接，请确认该页面是小宇宙 episode 地址")

        title = get_meta("og:title") or "未知标题"
        cover = get_meta("og:image")
        description = get_meta("og:description")

        return {
            "audio_url": audio_url,
            "title": title,
            "cover": cover,
            "description": description,
        }
