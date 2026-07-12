import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import itertools

# 🔗 スプレッドシートのURL（ID組み込み済み）
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1p34mv6TbY0z_P0iGCWEP-fJ9TYtPUObtvNYWcK-sXKs/edit?usp=sharing"

st.set_page_config(page_title="リーグ初期化ツール", layout="wide")
st.title("⚾ リーグ初期化スクリプト (H&A・ホーム数完全均等版)")
st.markdown("※ `team1` がホーム（後攻）、`team2` がアウェイ（先攻）として出力されます。")

# スプレッドシート接続
conn = st.connection("gsheets", type=GSheetsConnection)

# 変数をあらかじめ空で初期化
teams_df = None

# 1. 安全なデータ読み込み処理
try:
    teams_df = conn.read(spreadsheet=SPREADSHEET_URL, worksheet="teams", ttl=0)
except Exception as e:
    st.error("❌ Googleスプレッドシートの読み込みに失敗しました。")
    st.warning(f"**発生したエラーの詳細:** {e}")
    st.markdown("""
    ### 🛠️ エラーが出る場合の最終チェックリスト
    1. **アクセス権限**: スプレッドシートの右上にある「共有」ボタンから、サービスアカウントのメールアドレス（`~@~.iam.gserviceaccount.com`）が**「編集者」**として追加されているか確認してください。
    2. **シート名（タブ名）**: スプレッドシートの下側にあるタブの名前が、半角小文字の **`teams`** になっているか確認してください。
    """)

# 2. 読み込みに成功した場合のみ、以下の画面表示と生成ボタンの処理を行う
if teams_df is not None:
    st.subheader("🏃 現在登録されているチームマスタ")
    st.dataframe(teams_df, width="stretch")

    st.markdown("---")
    st.subheader("🔥 対戦プールの生成と書き込み")
    st.info("下のボタンを押すと、リーグ内総当たり2回、リーグ外総当たり1回の計40試合が自動生成され、スプレッドシートの `match_pool` タブへ上書き保存されます。")

    if st.button("全40試合の対戦プールを生成して書き込む", type="primary"):
        # リーグごとにチームを分類
        league_a = teams_df[teams_df['league'] == 'A']['team'].tolist()
        league_b = teams_df[teams_df['league'] == 'B']['team'].tolist()
        
        # 各リーグに4チームずつあるかチェック
        if len(league_a) != 4 or len(league_b) != 4:
            st.error(f"❌ エラー: リーグA、リーグBにそれぞれ4チームずつ登録されている必要があります。(現在: A={len(league_a)}チーム, B={len(league_b)}チーム)")
        else:
            with st.spinner("ホーム＆アウェイのバランスを計算しながら生成中..."):
                try:
                    matches = []
                    
                    # -------------------------------------------------------------
                    # ① リーグ内総当たり (H&Aを完全に入れ替え: 各チーム ホーム3 / アウェイ3)
                    # -------------------------------------------------------------
                    # リーグA内
                    for t1, t2 in itertools.combinations(league_a, 2):
                        matches.append({"team1": t1, "team2": t2})  # 1回戦: t1がホーム
                        matches.append({"team1": t2, "team2": t1})  # 2回戦: t2がホーム
                        
                    # リーグB内
                    for t1, t2 in itertools.combinations(league_b, 2):
                        matches.append({"team1": t1, "team2": t2})  # 1回戦: t1がホーム
                        matches.append({"team1": t2, "team2": t1})  # 2回戦: t2がホーム
                        
                    # -------------------------------------------------------------
                    # ② リーグ外対戦 (全チームが綺麗に ホーム2 / アウェイ2 になるマトリクス)
                    # -------------------------------------------------------------
                    # 各チームのホーム・アウェイ数が年間で偏らないようにするための特殊な配置図
                    home_matrix = [
                        [True,  True,  False, False],  # A1チーム目
                        [True,  False, True,  False],  # A2チーム目
                        [False, True,  False, True ],  # A3チーム目
                        [False, False, True,  True ]   # A4チーム目
                    ]
                    
                    for i in range(4):      # リーグAのインデックス
                        for j in range(4):  # リーグBのインデックス
                            t_a = league_a[i]
                            t_b = league_b[j]
                            
                            if home_matrix[i][j]:
                                # リーグAのチームがホーム
                                matches.append({"team1": t_a, "team2": t_b})
                            else:
                                # リーグBのチームがホーム
                                matches.append({"team1": t_b, "team2": t_a})
                                
                    # -------------------------------------------------------------
                    # データの保存と表示
                    # -------------------------------------------------------------
                    pool_df = pd.DataFrame(matches)
                    
                    # スプレッドシートの `match_pool` シートを上書き更新
                    conn.update(spreadsheet=SPREADSHEET_URL, worksheet="match_pool", data=pool_df)
                    
                    st.success(f"🎉 正常に全 {len(pool_df)} 試合の組み合わせを生成し、'match_pool' シートに保存しました！")
                    st.balloons()
                    
                    # 📊 均等に分かれているか検証データを画面に表示
                    st.subheader("📊 各チームの年間ホーム試合数チェック (全チーム 5 になっていれば大成功)")
                    home_counts = pool_df['team1'].value_counts().to_frame().rename(columns={'count': 'ホーム試合数（後攻）'})
                    st.dataframe(home_counts, width="stretch")
                    
                    st.subheader("👀 生成された全対戦カード（1〜40試合）")
                    st.dataframe(pool_df, width="stretch")
                
                except Exception as update_error:
                    st.error(f"❌ スプレッドシートへの書き込みに失敗しました。`match_pool` という名前のシート（タブ）が本当に存在するか確認してください。")
                    st.warning(f"詳細なエラー: {update_error}")