import streamlit as st
import feedparser
import os
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv
import urllib.parse
from datetime import datetime
from notion_client import Client
import schedule
import time
import threading

# 1. 환경 변수 로드
load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
notion_api_key = os.getenv("NOTION_API_KEY")
notion_db_id = os.getenv("NOTION_DATABASE_ID")

# 필수 키 검증
if not openai_api_key:
    st.error("⛔ OpenAI API 키가 없습니다.")
    st.stop()

if not notion_api_key or not notion_db_id:
    st.error("⛔ Notion 설정이 누락되었습니다.")
    st.stop()

# 클라이언트 설정
client = OpenAI(
    base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1",
    api_key=openai_api_key
)
notion = Client(auth=notion_api_key)
MODEL_NAME = "gpt-5-nano"

# 2. 핵심 기능 함수들

def get_real_url(rss_link):
    """Google RSS 링크의 실제 주소를 추적 (리다이렉트 해결)"""
    try:
        res = requests.head(rss_link, allow_redirects=True, timeout=5)
        return res.url
    except:
        return rss_link

def crawl_article(url):
    """뉴스 기사 본문 크롤링 (스크래핑)"""
    try:
        # 봇 차단 방지를 위한 헤더 설정
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code != 200:
            return "본문 수집 실패 (접근 제한)"

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 대부분의 뉴스 사이트는 <p> 태그에 본문이 있음
        paragraphs = soup.find_all('p')
        content = " ".join([p.get_text() for p in paragraphs])
        
        # 내용이 너무 짧으면 수집 실패로 간주
        if len(content) < 50:
            return "본문 수집 실패 (내용 없음)"
            
        return content[:3000] # LLM 입력 제한을 고려해 3000자까지만
    except Exception as e:
        return f"크롤링 오류: {str(e)}"

def fetch_google_news(keyword):
    """RSS 수집 + 본문 크롤링 통합"""
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)
    
    news_items = []
    # 속도를 위해 상위 2개만 수집
    for entry in feed.entries[:2]:
        real_url = get_real_url(entry.link)
        content = crawl_article(real_url)
        
        news_items.append({
            "title": entry.title,
            "link": real_url,
            "pubDate": entry.published,
            "content": content
        })
    return news_items

def summarize_news(news_data, query):
    """본문 내용을 포함한 고품질 요약"""
    prompt_text = ""
    for idx, item in enumerate(news_data, 1):
        prompt_text += f"\n[기사 {idx}: {item['title']}]\n본문내용: {item['content']}\n"

    system_prompt = f"사용자가 '{query}'에 대해 검색했어. 위 기사들의 '본문내용'을 바탕으로 핵심 정보를 종합해서 3줄로 깔끔하게 요약해줘."
    
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text}
        ]
    )
    return response.choices[0].message.content

def save_to_notion(query, summary, link=None):
    """Notion 저장"""
    try:
        notion.pages.create(
            parent={"database_id": notion_db_id},
            properties={
                "검색어": {"title": [{"text": {"content": query}}]},
                "요약내용": {"rich_text": [{"text": {"content": summary}}]},
                "날짜": {"date": {"start": datetime.now().isoformat()}},
                "링크": {"url": link if link else None}
            }
        )
        print(f"[Log] Notion saved: {query}")
        return True
    except Exception as e:
        print(f"[Error] Notion save failed: {e}")
        return False

# 3. 자동화 스케줄링 로직

def scheduled_job():
    """매일 실행될 자동 수집 작업"""
    print("⏰ 자동 수집 시작...")
    target_keyword = "최신 AI 기술" # 자동 수집할 주제
    items = fetch_google_news(target_keyword)
    if items:
        summary = summarize_news(items, target_keyword)
        save_to_notion(f"[자동] {target_keyword}", summary, items[0]['link'])
    print("✅ 자동 수집 완료")

def start_scheduler():
    """백그라운드에서 스케줄러 실행"""
    # 테스트를 위해 '매 분' 마다 실행 (배포 시엔 .every().day.at("09:00") 등으로 변경)
    schedule.every().day.at("09:00").do(scheduled_job) 
    
    while True:
        schedule.run_pending()
        time.sleep(1)

# Streamlit 실행 시 스케줄러 스레드 시작 (한 번만)
if "scheduler_started" not in st.session_state:
    t = threading.Thread(target=start_scheduler, daemon=True)
    t.start()
    st.session_state.scheduler_started = True

# 4. Streamlit UI (기존과 동일하되 검색 시 크롤링 적용)

st.title("📰 AI 뉴스 봇 (크롤링 & 자동화)")

with st.sidebar:
    st.header("설정 및 정보")
    st.markdown("[👉 Notion 바로가기](https://www.notion.so)")
    st.info("오전 9시마다 '최신 AI 기술' 뉴스를 자동으로 수집합니다.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("검색할 뉴스 주제를 입력하세요."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        
        # 간단한 의도 판별 (규칙 기반으로 속도 향상)
        if prompt in ["안녕", "반가워"]:
            full_response = "안녕하세요! 무엇을 도와드릴까요?"
        else:
            message_placeholder.markdown("🕵️ 기사 본문을 읽고 요약 중입니다... (시간이 조금 걸려요)")
            
            items = fetch_google_news(prompt)
            if items:
                summary = summarize_news(items, prompt)
                save_to_notion(prompt, summary, items[0]['link'])
                
                full_response = f"**['{prompt}' 심층 요약]**\n\n{summary}\n\n**출처:**"
                for item in items:
                    full_response += f"\n- [{item['title']}]({item['link']})"
            else:
                full_response = "관련 기사를 찾지 못했거나 접근이 제한되었습니다."

        message_placeholder.markdown(full_response)
        st.session_state.messages.append({"role": "assistant", "content": full_response})