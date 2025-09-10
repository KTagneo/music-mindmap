# app.py

import os
import time
import json
from flask import Flask, session, request, redirect, render_template, url_for, jsonify
from googleapiclient.discovery import build
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from pathlib import Path
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import pylast


load_dotenv()

app = Flask(__name__)

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# --- 데이터베이스 설정 추가 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'mydatabase.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}' # 우리 DB 파일의 이름과 위치
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # 불필요한 신호를 끄는 설정
db = SQLAlchemy(app) # 데이터베이스 객체 생성

# --- 데이터베이스 모델 정의 ---

# User 모델: 스포티파이 ID를 기준으로 사용자를 저장
class User(db.Model):
    id = db.Column(db.String(80), primary_key=True) # 스포티파이 유저 ID
    playlists = db.relationship('Playlist', backref='user', lazy=True)

# Playlist 모델: 사용자가 만든 플레이리스트(CD) 정보를 저장
class Playlist(db.Model):
    id = db.Column(db.Integer, primary_key=True) # 우리 앱에서의 고유 ID
    spotify_playlist_id = db.Column(db.String(80), nullable=False) # 스포티파이에서의 플레이리스트 ID
    name = db.Column(db.String(100), nullable=False) # 사용자가 지은 CD 이름
    user_id = db.Column(db.String(80), db.ForeignKey('user.id'), nullable=False) # 이 CD를 만든 User
    tracks = db.relationship('PlaylistTrack', backref='playlist', lazy=True)

# PlaylistTrack 모델: 어떤 플레이리스트에 어떤 곡이 들어있는지 저장
class PlaylistTrack(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    track_id = db.Column(db.String(80), nullable=False) # 스포티파이 트랙 ID
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=False)

# --- Spotify 인증 설정 ---
scope = "user-read-private user-read-email playlist-modify-public playlist-modify-private"
auth_manager = SpotifyOAuth(
    client_id=os.getenv('SPOTIPY_CLIENT_ID'),
    client_secret=os.getenv('SPOTIPY_CLIENT_SECRET'),
    redirect_uri="http://127.0.0.1:5000/callback",
    scope=scope
)

# --- Last.fm 네트워크 객체 생성 ---
lastfm_network = pylast.LastFMNetwork(
    api_key=os.getenv('LASTFM_API_KEY'),
    api_secret=os.getenv('LASTFM_API_SECRET')
)

@app.route('/')
def home():
    return render_template('index.html', session=session)

@app.route('/login')
def login():
    auth_url = auth_manager.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get("code")
    token_info = auth_manager.get_access_token(code)
    session["token_info"] = token_info
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop("token_info", None)
    return redirect('/')

@app.route('/search')
def search():
    session['seen_tracks'] = []

    token_info = get_token()
    if not token_info:
        return redirect('/login')
    sp = spotipy.Spotify(auth=token_info['access_token'])
    query = request.args.get('query')
    if not query:
        return redirect('/')
    results = sp.search(q=query, type='track', limit=10)
    tracks = results['tracks']['items']


    return render_template('search_results.html', tracks=tracks, query=query)

# 세션에 seentrack 리스트 생성 > 검색한 centertrack의 id를 세션에 추가 > 
# 추천 곡 검색 시 6+리스트 길이만큼 뽑기(lastfm) > spotify에 1ㄷ1로 검색, id 겹치면 rec 리스트에 안 넣기 
@app.route('/recommendations/<track_id>')
def get_recommendations(track_id):

    token_info = get_token()
    if not token_info:
        return redirect('/login')
    
    # 1. 세션에서 '본 곡' 목록을 가져오고, 없으면 새로 만듭니다.
    seen_tracks = session.get('seen_tracks', [])
    # 현재 중심 트랙을 '본 곡' 목록에 추가합니다.
    if track_id not in seen_tracks:
        seen_tracks.append(track_id)
    session['seen_tracks'] = seen_tracks
    
    sp = spotipy.Spotify(auth=token_info['access_token'])

    try:
        center_track = sp.track(track_id)
        center_track_name = center_track['name']
        center_track_artist = center_track['artists'][0]['name']

        # 2. 넉넉하게 추천곡을 요청합니다. (목표 6개 + 본 곡 수 + 여유 5개)
        request_limit = 6 + len(seen_tracks) + 5
        lastfm_track = lastfm_network.get_track(center_track_artist, center_track_name)
        similar_tracks_lastfm = lastfm_track.get_similar(limit=request_limit)

        recommendations = []
        for lastfm_item in similar_tracks_lastfm:
            item = lastfm_item.item
            query = f"track:{item.title} artist:{item.artist.name}"
            spotify_results = sp.search(q=query, type='track', limit=1)
            
            if spotify_results['tracks']['items']:
                spotify_track = spotify_results['tracks']['items'][0]
                # 3. '본 곡' 목록에 없는 곡만 추천 리스트에 추가합니다.
                if spotify_track['id'] not in seen_tracks:
                    recommendations.append(spotify_track)
            
            if len(recommendations) >= 6:
                break
        
        return render_template('mindmap.html', center_track=center_track, recommendations=recommendations)

    except Exception as e:
        print(f"추천 생성 중 에러 발생: {e}")
        return f"추천 목록을 생성하는 중 에러가 발생했습니다.: {e} <a href='/'>돌아가기</a>"

# app.py의 get_video_id 함수를 교체

@app.route('/api/get-video-id')
def get_video_id():
    track_name = request.args.get('track')
    artist_name = request.args.get('artist')

    if not track_name or not artist_name:
        return jsonify({'error': '필수 파라미터가 없습니다'}), 400

    try:
        youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))

        # 1. 더 정확한 검색어 생성
        search_query = f"{artist_name} {track_name} Official Audio"

        # 2. 넉넉하게 5개의 검색 결과를 요청
        search_response = youtube.search().list(
            q=search_query,
            part='snippet',
            maxResults=5,
            type='video',
            videoCategoryId='10'
        ).execute()

        items = search_response.get('items', [])
        if not items:
            return jsonify({'error': '결과를 찾을 수 없습니다'}), 404

        # 4. 5개의 결과 중에서 우선순위에 따라 최고의 영상 선택
        best_video_id = None
        
        # 1순위: 제목에 'Official Audio'가 포함된 영상
        for item in items:
            title = item['snippet']['title'].lower()
            if 'official audio' in title:
                best_video_id = item['id']['videoId']
                break # 찾았으면 바로 종료
        
        # 2순위: 1순위가 없으면 'Topic' 채널의 영상을 찾음
        if not best_video_id:
            for item in items:
                channel_title = item['snippet']['channelTitle'].lower()
                if 'topic' in channel_title:
                    best_video_id = item['id']['videoId']
                    break
        
        # 3순위: 1, 2순위가 모두 없으면 그냥 첫 번째 결과를 사용
        if not best_video_id:
            best_video_id = items[0]['id']['videoId']

        return jsonify({'video_id': best_video_id})

    except Exception as e:
        print(f"YouTube API Error: {e}")
        return jsonify({'error': 'YouTube API 처리 중 에러 발생'}), 500

@app.route('/select-tracks')
def select_tracks():
    token_info = get_token()
    if not token_info:
        return redirect('/login')

    # 세션에서 탐색한 트랙 ID 목록을 가져옴
    seen_track_ids = session.get('seen_tracks', [])
    if not seen_track_ids:
        # 본 곡이 없으면 메인으로 보냄
        return redirect('/')

    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    # 여러 트랙의 상세 정보를 한 번의 API 호출로 가져옴 (효율적!)
    track_details = sp.tracks(tracks=seen_track_ids)

    # 템플릿에 트랙 상세 정보 목록을 전달
    return render_template('select_tracks.html', tracks=track_details['tracks'])

@app.route('/create-cd', methods=['POST'])
def create_cd():
    playlist_name = request.form.get('playlist_name')
    track_ids = request.form.getlist('track_ids')

    if not track_ids or not playlist_name:
        return "선택된 곡이 없거나 CD 제목이 없습니다. <a href='/'>돌아가기</a>"

    token_info = get_token()
    if not token_info:
        return redirect('/login')

    sp = spotipy.Spotify(auth=token_info['access_token'])

    try:
        # --- 1. 사용자 확인 또는 생성 ---
        user_info = sp.current_user()
        user_id = user_info['id']
        
        # 우리 DB에 이 유저가 있는지 확인
        db_user = User.query.get(user_id)
        if not db_user:
            # 없으면 새로 만들어서 DB에 추가
            db_user = User(id=user_id)
            db.session.add(db_user)

        # --- 2. Spotify에 플레이리스트 생성 ---
        new_spotify_playlist = sp.user_playlist_create(
            user=user_id,
            name=playlist_name,
            public=False,
            description=f"'{playlist_name}' - Music Mindmap Store에서 생성됨"
        )
        spotify_playlist_id = new_spotify_playlist['id']
        
        track_uris = [f'spotify:track:{track_id}' for track_id in track_ids]
        sp.playlist_add_items(spotify_playlist_id, track_uris)

        # --- 3. 우리 DB에 플레이리스트(CD) 정보 저장 ---
        new_db_playlist = Playlist(
            spotify_playlist_id=spotify_playlist_id,
            name=playlist_name,
            user=db_user # User 객체와 연결
        )
        db.session.add(new_db_playlist)

        # --- 4. 우리 DB에 수록곡들 저장 ---
        for t_id in track_ids:
            new_track = PlaylistTrack(
                track_id=t_id,
                playlist=new_db_playlist # Playlist 객체와 연결
            )
            db.session.add(new_track)

        # --- 5. 모든 변경사항을 DB에 최종 저장 (Commit) ---
        db.session.commit()

        # --- 6. 성공 후 '내 CD 목록' 페이지로 이동 ---
        return redirect(url_for('my_cds'))

    except Exception as e:
        db.session.rollback() # 에러 발생 시, DB 변경사항을 모두 되돌림
        print(f"플레이리스트 생성 중 에러 발생: {e}")
        return f"플레이리스트 생성 중 에러가 발생했습니다: {e} <a href='/'>돌아가기</a>"


@app.route('/my-cds')
def my_cds():
    token_info = get_token()
    if not token_info:
        return redirect('/login')

    sp = spotipy.Spotify(auth=token_info['access_token'])
    user_info = sp.current_user()
    user_id = user_info['id']

    # 1. DB에서 현재 사용자를 찾음
    user = User.query.get(user_id)
    playlists = []
    if user:
        # 2. 사용자가 존재하면, 그 사용자가 만든 모든 플레이리스트를 가져옴
        playlists = user.playlists
    
    # 3. my_cds.html 템플릿에 플레이리스트 목록을 전달
    return render_template('my_cds.html', playlists=playlists)

@app.route('/cd/<int:playlist_id>')
def get_cd_details(playlist_id):
    token_info = get_token()
    if not token_info:
        return redirect('/login')

    # 1. DB에서 전달받은 id와 일치하는 플레이리스트를 찾음
    playlist = Playlist.query.get_or_404(playlist_id)
    
    # 2. 해당 플레이리스트에 속한 모든 트랙들의 ID 목록을 추출
    track_ids = [track.track_id for track in playlist.tracks]

    if not track_ids:
        # 수록곡이 없는 경우
        return render_template('playlist_detail.html', playlist=playlist, tracks=[])

    sp = spotipy.Spotify(auth=token_info['access_token'])
    
    # 3. 스포티파이에 트랙 ID 목록을 한꺼번에 보내 상세 정보를 받아옴
    track_details = sp.tracks(tracks=track_ids)

    # 4. 템플릿에 플레이리스트 정보와 트랙 상세 정보 목록을 전달
    return render_template('playlist_detail.html', playlist=playlist, tracks=track_details['tracks'])

def get_token():
    token_info = session.get("token_info", None)
    if not token_info:
        return None
    now = int(time.time())
    is_expired = token_info['expires_at'] - now < 60
    if (is_expired):
        token_info = auth_manager.refresh_access_token(token_info['refresh_token'])
        session["token_info"] = token_info
    return token_info

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)